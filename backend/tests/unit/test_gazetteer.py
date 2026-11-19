"""Mis-geotagged Wikipedia articles: the article's own stated location must agree with its coordinates."""

from __future__ import annotations

import pytest

from app.lite.gazetteer import location_conflict

PURI = (19.8048, 85.8180)
BHUBANESWAR = (20.2961, 85.8245)


@pytest.mark.parametrize(("title", "description", "where", "stated"), [
    ("Jagannath Temple, Bengaluru", "Hindu temple in Karnataka, India", PURI, "bengaluru"),
    ("City Centre Mall, Sambalpur", "Shopping mall in Odisha, India", BHUBANESWAR, "sambalpur"),
    ("Some Shrine", "Hindu temple in Tamil Nadu, India", PURI, "tamil nadu"),
    ("Replica Temple", "Replica of the Puri temple in Delhi", PURI, "delhi"),
])
def test_contradicting_location_is_caught(title, description, where, stated) -> None:
    assert location_conflict(title, description, *where) == stated


@pytest.mark.parametrize(("title", "description", "where"), [
    ("Jagannath Temple, Puri", "Hindu temple at Puri, Odisha, India", PURI),
    ("Atharanala", "Bridge in Puri town, Odisha (India)", PURI),
    ("Aruna Stambha", "Religious Pillar of Jagannath Temple at Puri, Odisha, India", PURI),
    ("Shri Jagannath Medical College and Hospital", "8th Medical College in Odisha located at Puri", PURI),
    ("Lingaraj Rest House", "Hindu temple in Orissa, India", BHUBANESWAR),          # old state name
    ("Ram Mandir, Bhubaneswar", "Hindu temple in Bhubaneswar, India", BHUBANESWAR),
    ("Konark Sun Temple", "13th-century Hindu temple in Konark, Odisha", (19.8876, 86.0945)),
    ("Jagannath Temple, Hauz Khas", "Hindu temple in Delhi, India", (28.5494, 77.2001)),
    ("Mukti Mandapa", None, PURI),                                                    # nothing stated
    ("Kalinga Stadium", "Multi-purpose stadium in Unknownpur", BHUBANESWAR),          # unknown name: no evidence
])
def test_consistent_or_unknown_location_is_kept(title, description, where) -> None:
    assert location_conflict(title, description, *where) is None
