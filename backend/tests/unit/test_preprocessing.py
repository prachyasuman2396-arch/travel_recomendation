from datetime import UTC, datetime

import pytest

from app.services.data_collection.base import FlickrPhotoRecord, PlaceRecord, YelpReviewRecord
from app.services.preprocessing.categories import (
    GOOGLE_TYPE_MAP,
    category_compatibility,
    map_types,
    primary_category,
)
from app.services.preprocessing.cleaning import (
    clean_photos,
    clean_places,
    clean_reviews,
    normalize_address,
    normalize_name,
    normalize_rating,
    text_hash,
    valid_coordinates,
)

NOW = datetime(2026, 11, 16, tzinfo=UTC)


def place(**kw) -> PlaceRecord:
    base = {"external_id": "g1", "name": "Lingaraj Temple", "latitude": 20.2382, "longitude": 85.8338,
            "data_origin": "mock", "fetched_at": NOW}
    return PlaceRecord(**(base | kw))


@pytest.mark.parametrize(("raw", "expected"), [
    ("  The Lingaraj   Temple ", "lingaraj temple"),
    ("Udayagiri & Khandagiri Caves", "udayagiri khandagiri caves"),
    ("Café Coffee Day!", "cafe coffee day"),
    (None, ""),
])
def test_normalize_name(raw, expected):
    assert normalize_name(raw) == expected


def test_normalize_address_expands_abbreviations_and_drops_country():
    assert normalize_address("Lewis Rd, BBSR, Odisha, India") == "lewis road bhubaneswar odisha"


@pytest.mark.parametrize(("lat", "lon", "ok"), [
    (20.29, 85.82, True), (95.0, 85.0, False), (20.0, 181.0, False), (0.0, 0.0, False),
    (None, 85.0, False), (float("nan"), 85.0, False),
])
def test_valid_coordinates(lat, lon, ok):
    assert valid_coordinates(lat, lon) is ok


@pytest.mark.parametrize(("value", "scale", "expected"), [
    (4.5, 5, 4.5), (9.0, 10, 4.5), (5.5, 5, None), (-1, 5, None), ("abc", 5, None), (None, 5, None),
])
def test_normalize_rating_rejects_out_of_range(value, scale, expected):
    assert normalize_rating(value, scale) == expected


def test_clean_places_rejects_and_dedupes():
    records = [
        place(),
        place(),  # duplicate external id
        place(external_id="g2", name="   "),
        place(external_id="g3", latitude=0.0, longitude=0.0),
        place(external_id="g4", name="Closed Park", business_status="CLOSED_PERMANENTLY"),
        place(external_id="g5", name="Bad Rating", rating=7.5),
    ]
    clean, rejected = clean_places(records)
    assert [r.external_id for r in clean] == ["g1", "g5"]
    assert clean[1].rating is None  # invalid rating dropped, record kept
    assert {r.reason for r in rejected} == {"empty name", "invalid coordinates", "permanently closed"}


def test_clean_reviews_requires_text_or_rating():
    base = {"business_external_id": "b", "data_origin": "mock", "fetched_at": NOW}
    clean, rejected = clean_reviews([
        YelpReviewRecord(external_id="r1", text="  Great  place ", rating=5, **base),
        YelpReviewRecord(external_id="r2", text=None, rating=None, **base),
    ])
    assert clean[0].text == "Great place"
    assert rejected[0].external_id == "r2"


def test_clean_photos_filters_bad_geo_and_normalises_tags():
    base = {"data_origin": "mock", "fetched_at": NOW}
    clean, rejected = clean_photos([
        FlickrPhotoRecord(external_id="p1", latitude=20.2, longitude=85.8, tags=("Lingaraj", "TEMPLE"), **base),
        FlickrPhotoRecord(external_id="p2", latitude=0, longitude=0, **base),
        FlickrPhotoRecord(external_id="p3", latitude=20.2, longitude=85.8, accuracy=3, **base),
    ])
    assert clean[0].tags == ("lingaraj", "temple")
    assert {r.reason for r in rejected} == {"invalid coordinates", "low geo accuracy"}


def test_category_mapping_and_compatibility():
    cats = map_types(["zoo", "tourist_attraction"], GOOGLE_TYPE_MAP)
    assert cats == ["wildlife", "nature"]
    assert primary_category(cats) == "wildlife"
    assert primary_category([]) == "cultural"
    assert category_compatibility(["food"], ["parks_gardens"]) == 0.0
    assert category_compatibility(["religious"], ["historical"]) == 1.0
    assert category_compatibility([], ["nature"]) is None


def test_text_hash_ignores_whitespace_differences():
    assert text_hash("Nice  place") == text_hash(" Nice place ")
    assert text_hash(None) is None
