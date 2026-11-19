"""Deterministic ranker: data gaps shared by every place are stated once, not repeated on each card."""

from __future__ import annotations

from app.services.recommendation.fallback import templated_response

NOISE = ("No rating data available", "Not enough review data for sentiment insights",
         "Opening hours unknown — check before visiting")


def _cand(pid: str, *, rating=None, hours="unknown", sentiment=None) -> dict:
    return {"id": pid, "category": "historical", "matched_interests": ["historical"], "distance_km": 1.0,
            "time_fit": "good", "rating": rating, "rating_count": 10 if rating else 0,
            "review_sentiment": sentiment, "opening_hours_today": hours, "weather_fit_hint": "suitable",
            "notices": [], "facts": []}


def _payload(cands: list[dict]) -> dict:
    return {"candidates": cands, "preferences": {"available_hours": 4}, "weather": None}


def test_gaps_shared_by_all_places_go_to_the_summary_once() -> None:
    out = templated_response(_payload([_cand("P1"), _cand("P2"), _cand("P3")]), 6)
    for rec in out["recommendations"]:
        assert not set(rec["considerations"]) & set(NOISE)
    assert "No ratings, reviews or opening hours are available for places in this area" in out["summary"]
    assert "check timings locally" in out["summary"]


def test_gaps_still_flagged_per_card_when_other_places_have_the_data() -> None:
    good = _cand("P1", rating=4.5, hours="6:00 AM – 9:00 PM",
                 sentiment={"positive_aspects": ["views"], "negative_aspects": []})
    out = templated_response(_payload([good, _cand("P2")]), 6)
    by_id = {r["poi_id"]: r for r in out["recommendations"]}
    assert set(NOISE) <= set(by_id["P2"]["considerations"])
    assert not set(NOISE) & set(by_id["P1"]["considerations"])
    assert "available for places in this area" not in out["summary"]
