"""Deterministic, template-based ranking.

Used (a) by the offline ``mock`` LLM provider and (b) as the *opt-in*
RECOMMENDATION_DETERMINISTIC_FALLBACK when every real provider fails.
Both are labelled in the API response; neither is presented as AI output.
Explanations are assembled only from facts present in the candidate payload.
"""

from __future__ import annotations

INTEREST_LABELS = {"parks_gardens": "parks & gardens"}


def _label(interest: str) -> str:
    return INTEREST_LABELS.get(interest, interest)


def templated_response(payload: dict, max_results: int) -> dict:
    """``payload`` is the exact LLM user payload; candidates are already sorted best-first."""
    weather_known = payload.get("weather") is not None
    hours = payload["preferences"]["available_hours"]
    recs = []
    for c in payload["candidates"]:
        if c["time_fit"] == "unlikely":
            continue
        matched = c.get("matched_interests") or []
        reason_bits = []
        if matched:
            reason_bits.append(f"Matches your interest in {', '.join(_label(m) for m in matched)}")
        else:
            reason_bits.append(f"A {_label(c['category'])} destination within your search radius")
        reason_bits.append(f"{c['distance_km']} km away")
        advantages, considerations, insights = [], [], []
        if c.get("rating") is not None:
            advantages.append(f"Rated {c['rating']} from {c['rating_count']} ratings")
        else:
            considerations.append("No rating data available")
        sentiment = c.get("review_sentiment")
        if isinstance(sentiment, dict):
            if sentiment.get("positive_aspects"):
                insights.append("Reviewers were positive about: " + ", ".join(sentiment["positive_aspects"]))
            if sentiment.get("negative_aspects"):
                considerations.append("Reviewers raised: " + ", ".join(sentiment["negative_aspects"]))
        else:
            considerations.append("Not enough review data for sentiment insights")
        if c.get("opening_hours_today") == "unknown":
            considerations.append("Opening hours unknown — check before visiting")
        else:
            advantages.append(f"Listed hours today: {c['opening_hours_today']}")
        if c["time_fit"] == "tight":
            considerations.append(f"May be tight for {hours} hours")
        for notice in c.get("notices") or []:
            if notice["severity"] in ("critical", "warning"):
                considerations.insert(0, notice["text"])
            else:
                advantages.append(notice["text"])
        if c.get("season_note"):
            considerations.append(c["season_note"])
        cited = [f["id"] for f in c.get("facts") or []][:4]
        weather = c["weather_fit_hint"] if weather_known else "unknown"
        if weather == "unsuitable":
            considerations.append("Current weather is not ideal for this outdoor place")
        recs.append({
            "poi_id": c["id"], "rank": len(recs) + 1, "reason": ". ".join(reason_bits) + ".",
            "matched_preferences": [_label(m) for m in matched],
            "considerations": considerations[:8], "weather_suitability": weather, "review_insights": insights,
            "advantages": advantages[:8], "cited_fact_ids": cited,
        })
        if len(recs) >= max_results:
            break
    summary = (f"{len(recs)} destinations ranked by a deterministic score combining interest match, "
               "ratings, distance, review sentiment and weather.") if recs else "No suitable destinations found."
    return {"recommendations": recs, "summary": summary}
