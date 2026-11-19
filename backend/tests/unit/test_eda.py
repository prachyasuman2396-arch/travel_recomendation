import json

import pandas as pd

from app.eda.report import EdaFrames, compute_summary, write_report


def frames() -> EdaFrames:
    pois = pd.DataFrame([
        {"poi_id": "POI1", "name": "A", "normalized_name": "a", "category": "nature", "latitude": 20.3,
         "longitude": 85.8, "data_origin": "mock", "description": None, "has_hours": True, "has_website": False,
         "address": "x"},
        {"poi_id": "POI2", "name": "A", "normalized_name": "a", "category": "religious", "latitude": 20.2,
         "longitude": 85.9, "data_origin": "mock", "description": "d", "has_hours": False, "has_website": True,
         "address": None},
    ])
    return EdaFrames(
        pois=pois,
        sources=pd.DataFrame([{"poi_id": "POI1", "provider": "google_places", "matching_method": "origin",
                               "matching_confidence": 1.0},
                              {"poi_id": "POI1", "provider": "yelp", "matching_method": "auto",
                               "matching_confidence": 0.86}]),
        ratings=pd.DataFrame([{"poi_id": "POI1", "source": "google_places", "rating": 4.4, "rating_count": 100}]),
        reviews=pd.DataFrame([{"poi_id": "POI1", "source": "yelp", "rating": 5, "has_text": True}]),
        photos=pd.DataFrame([{"poi_id": "POI1", "match_confidence": 0.9, "match_method": "geo+text"}]),
        candidates=pd.DataFrame([{"provider": "yelp", "score": 0.7, "status": "pending"}]),
        sentiment=pd.DataFrame([{"poi_id": "POI1", "status": "insufficient_data", "overall": None}]),
    )


def test_summary_counts_are_exact():
    s = compute_summary(frames())
    assert s["total_destinations"] == 2
    assert s["possible_duplicate_names"] == 2
    assert s["missing_values"] == {"description": 1, "address": 1, "opening_hours": 1, "website": 1,
                                   "coordinates": 0}
    assert s["source_coverage"]["pois_with_multiple_providers"] == 1
    assert s["entity_matching"]["flagged_for_review"] == 1
    assert s["flickr_photos"]["pois_with_photos"] == 1
    assert s["data_origin"] == {"mock": 2}


def test_report_files_written(tmp_path):
    out = write_report(frames(), tmp_path)
    assert (out / "summary.json").exists() and (out / "categories.png").exists() and (out / "geo.png").exists()
    assert json.loads((out / "summary.json").read_text())["total_destinations"] == 2
