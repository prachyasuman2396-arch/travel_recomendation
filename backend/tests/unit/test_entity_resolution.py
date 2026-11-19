import pytest

from app.services.entity_resolution.matcher import (
    EntityMatcher,
    EntityView,
    MatchConfig,
    MatchOutcome,
    associate_photo,
)

GARDEN = EntityView("POI1", "Ekamra Kanan Botanical Gardens", 20.3045, 85.8180, ("parks_gardens", "nature"),
                    "Nayapalli, Bhubaneswar, Odisha 751015")
ZOO = EntityView("POI2", "Nandankanan Zoological Park", 20.3958, 85.8189, ("wildlife", "nature"),
                 "Barang, Bhubaneswar, Odisha 754005")
TEMPLE = EntityView("POI3", "Lingaraj Temple", 20.2382, 85.8338, ("religious", "historical"),
                    "Lingaraj Rd, Old Town, Bhubaneswar, Odisha 751002")


@pytest.fixture
def matcher() -> EntityMatcher:
    return EntityMatcher(MatchConfig())


def test_name_variant_across_providers_is_high_confidence(matcher):
    yelp = EntityView("y1", "Nandankanan Zoo", 20.3965, 85.8178, ("wildlife", "nature"), "Bhubaneswar, Odisha")
    result = matcher.best_match(yelp, [ZOO, GARDEN])
    assert result.outcome == MatchOutcome.HIGH
    assert result.candidate_key == "POI2"


def test_spelling_variant_matches(matcher):
    yelp = EntityView("y2", "Lingaraja Temple", 20.2384, 85.8335, ("religious",), None)
    assert matcher.best_match(yelp, [TEMPLE]).outcome == MatchOutcome.HIGH


def test_cafe_next_to_garden_is_not_merged(matcher):
    """The canonical failure mode: 25–30 m apart, overlapping name, different kind of place."""
    cafe = EntityView("y3", "Ekamra Kanan Cafe", 20.3047, 85.8182, ("food",), "Bhubaneswar, Odisha")
    result = matcher.score_pair(cafe, GARDEN)
    assert result.breakdown["distance_m"] < 40
    assert result.outcome == MatchOutcome.LOW
    assert result.breakdown["category"] == 0.0


def test_proximity_alone_never_merges(matcher):
    other = EntityView("y4", "Sharma Electronics", 20.3046, 85.8181, (), None)
    result = matcher.score_pair(other, GARDEN)
    assert result.outcome == MatchOutcome.LOW
    assert result.breakdown["reason"] == "name_too_different"


def test_far_records_are_not_candidates(matcher):
    far = EntityView("y5", "Lingaraj Temple", 20.2600, 85.8338, ("religious",), None)  # ~2.4 km away
    result = matcher.score_pair(far, TEMPLE)
    assert result.outcome == MatchOutcome.LOW and result.breakdown["reason"] == "too_far"


def test_same_name_far_apart_pin_is_flagged_for_review(matcher):
    rajarani = EntityView("POI7", "Rajarani Temple", 20.2440, 85.8436, ("religious", "historical"), None)
    far = EntityView("y6", "Raja Rani Temple", 20.2474, 85.8436, ("historical", "cultural"), None)  # ~380 m
    result = matcher.best_match(far, [rajarani])
    assert result.outcome == MatchOutcome.MEDIUM


@pytest.mark.parametrize(("a", "b"), [
    ("Dhauli Peace Pagoda", "Dhauli Shanti Stupa"),       # shanti = peace, pagoda ~ stupa
    ("Bindusagar", "Bindu Sagar Lake"),                    # compound spelling
    ("Raja Rani Mandir", "Rajarani Temple"),               # mandir = temple + spacing
    ("Shree Jagannath Mandir", "Jagannath Temple"),        # honorific + synonym
])
def test_indian_name_variants_match(matcher, a, b):
    rec = EntityView("x", a, 20.2400, 85.8400, ("religious",), None)
    poi = EntityView("P", b, 20.2401, 85.8401, ("religious",), None)
    assert matcher.score_pair(rec, poi).outcome == MatchOutcome.HIGH


def test_generic_words_do_not_merge_different_places(matcher):
    """'Odisha ... Museum' twice, 30 m apart: two different museums."""
    state = EntityView("P6", "Odisha State Museum", 20.2575, 85.8418, ("cultural",), None)
    crafts = EntityView("x", "Odisha Crafts Museum", 20.2577, 85.8420, ("cultural",), None)
    result = matcher.score_pair(crafts, state)
    assert result.outcome == MatchOutcome.LOW and result.breakdown["distinctive_conflict"] is True


def test_ambiguous_between_two_candidates_is_flagged(matcher):
    a = EntityView("POIa", "Ram Mandir", 20.2867, 85.8415, ("religious",), None)
    b = EntityView("POIb", "Ram Mandir", 20.2868, 85.8416, ("religious",), None)
    rec = EntityView("y7", "Ram Mandir", 20.28675, 85.84155, ("religious",), None)
    result = matcher.best_match(rec, [a, b])
    assert result.outcome == MatchOutcome.MEDIUM
    assert result.breakdown["reason"] == "ambiguous_between_candidates"


def test_thresholds_are_configurable():
    strict = EntityMatcher(MatchConfig(high_threshold=0.99, medium_threshold=0.98))
    yelp = EntityView("y1", "Nandankanan Zoo", 20.3965, 85.8178, ("wildlife",), None)
    assert strict.best_match(yelp, [ZOO]).outcome == MatchOutcome.LOW


def test_no_candidates_returns_low(matcher):
    result = matcher.best_match(EntityView("x", "Anything", 20.0, 85.0), [])
    assert result.outcome == MatchOutcome.LOW and result.candidate_key is None


def test_photo_with_matching_tags_is_associated():
    poi_id, conf, method = associate_photo("Morning walk", ("ekamrakanan", "flowers"), 20.3046, 85.8181,
                                           [GARDEN, ZOO])
    assert poi_id == "POI1" and method == "geo+text" and conf >= 0.55


def test_untagged_photo_next_to_poi_is_not_associated():
    poi_id, conf, method = associate_photo("IMG_0001", (), 20.3046, 85.8181, [GARDEN])
    assert poi_id is None and method == "geo_only" and conf < 0.55
