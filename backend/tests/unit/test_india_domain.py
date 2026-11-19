"""India domain: name normalisation, categories, seasons, fact values and visitor rules."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.services.india.facts import FactView, facts_by_key, validate_fact_value
from app.services.india.rules import EventView, VisitorProfile, apply_rules
from app.services.india.seasons import in_india, indian_season, season, season_category_adjustment
from app.services.preprocessing.categories import categories_from_name
from app.services.preprocessing.cleaning import extract_pin, normalize_name


# ------------------------------------------------------------------ names
@pytest.mark.parametrize(("raw", "expected"), [
    ("Shri Lingaraj Mandir", "lingaraj temple"),
    ("Bindu Sagar", "bindu lake"),
    ("Dhauli Shanti Stupa", "dhauli peace stupa"),
    ("Ekamra Kanan", "ekamra garden"),
])
def test_indian_synonyms_and_honorifics(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected


def test_raw_names_keep_local_words_when_not_canonical() -> None:
    assert "kanan" in normalize_name("Ekamra Kanan", canonical=False)


@pytest.mark.parametrize("name", ["ଲିଙ୍ଗରାଜ ମନ୍ଦିର", "लिंगराज मंदिर"])
def test_indic_script_survives_normalisation(name: str) -> None:
    out = normalize_name(name)
    # Combining vowel signs are not \w; they must not be shredded into separate tokens.
    assert out == " ".join(name.split())
    assert len(out.split()) == 2


def test_punctuation_is_removed_but_letters_kept() -> None:
    assert normalize_name("Udayagiri & Khandagiri Caves!") == "udayagiri khandagiri caves"


@pytest.mark.parametrize(("address", "pin"), [
    ("Old Town, Bhubaneswar, Odisha 751002, India", "751002"),
    ("Puri - 752 001", "752001"),
    ("No PIN here", None),
])
def test_extract_pin(address: str, pin: str | None) -> None:
    assert extract_pin(address) == pin


@pytest.mark.parametrize(("name", "cats"), [
    ("Deras Dam", ["nature"]),
    ("Bindu Sagar Lake", ["nature"]),
    ("Atri Hot Spring", ["nature"]),
    ("Duduma Falls", ["waterfalls", "nature"]),
    ("Barabati Fort", ["historical"]),
    ("Kalinga Stadium", []),
])
def test_categories_from_name_hints(name: str, cats: list[str]) -> None:
    assert categories_from_name(name) == cats


def test_name_hints_are_whole_word() -> None:
    assert categories_from_name("Parkview Hotel") == []  # 'park' must not match inside 'parkview'


# ---------------------------------------------------------------- seasons
@pytest.mark.parametrize(("month", "name"), [(4, "summer"), (7, "monsoon"), (11, "post_monsoon"), (1, "winter")])
def test_indian_seasons(month: int, name: str) -> None:
    assert indian_season(date(2026, month, 10)) == name


def test_season_outside_india_uses_generic_calendar() -> None:
    assert in_india(20.29, 85.82) and not in_india(48.85, 2.35)
    assert season(datetime(2026, 7, 1), 48.85, 2.35) != "monsoon"


def test_monsoon_favours_waterfalls() -> None:
    adj, note = season_category_adjustment("monsoon", ["waterfalls", "nature"])
    assert adj > 0 and note


# -------------------------------------------------------------- fact values
def test_fact_values_are_schema_checked() -> None:
    assert validate_fact_value("entry_fee", {"indian": 40, "foreign": 600, "currency": "INR"})
    assert validate_fact_value("entry_fee", {"indian": "forty"}) is None
    assert validate_fact_value("not_a_key", {"x": 1}) is None


def _fact(key: str, value: dict, status: str = "verified", fid: str = "1") -> FactView:
    return FactView(fid, key, value, f"{key} text", status, "curated", "Source", "https://example.org/src")


def test_verified_fact_wins_over_unverified() -> None:
    facts = [_fact("entry_fee", {"indian": 99}, "unverified", "a"), _fact("entry_fee", {"indian": 40}, "verified", "b")]
    assert facts_by_key(facts)["entry_fee"].value["indian"] == 40


# -------------------------------------------------------------------- rules
NON_HINDU = VisitorProfile(non_hindu_visitor=True)


def test_verified_no_entry_without_platform_excludes() -> None:
    out = apply_rules(NON_HINDU, "religious", ["religious"],
                      [_fact("non_hindu_entry", {"allowed": False, "viewing_platform": None})], [])
    assert out.excluded and out.reason == "entry_restricted"


def test_verified_no_entry_with_platform_still_excludes() -> None:
    # Lingaraj: a viewing platform outside the wall is not entry, so the temple is never a stop.
    out = apply_rules(NON_HINDU, "religious", ["religious"],
                      [_fact("non_hindu_entry", {"allowed": False, "viewing_platform": True})], [])
    assert out.excluded and out.reason == "entry_restricted"


@pytest.mark.parametrize(("name", "category", "lat", "lon", "description"), [
    ("Lingaraj Temple", "religious", 20.2382, 85.8338, None),
    ("Guruvayur Sri Krishna Temple", "religious", 10.5946, 76.0394, None),
    ("Sree Padmanabhaswamy Temple", "religious", 8.4828, 76.9436, None),
    ("Parvati Temple", "religious", 20.2384, 85.8340, None),            # inside the Lingaraj compound
    ("Gundicha Temple", "religious", 19.8133, 85.8306, None),
    ("Jagannath Temple", "cultural", 19.8048, 85.8180,                   # "school" in text must not unblock it
     "Built in the Kalinga school of architecture near the railway station and beach."),
    ("Some Shiva Temple", "religious", 22.0, 84.0, "Entry is restricted to Hindus only."),
])
def test_orthodox_shrines_are_excluded_for_non_hindus(name, category, lat, lon, description) -> None:
    out = apply_rules(NON_HINDU, category, [category], [], [], poi_name=name, description=description,
                      lat=lat, lon=lon)
    assert out.excluded and out.reason == "entry_restricted", name


@pytest.mark.parametrize(("name", "category", "lat", "lon", "description"), [
    ("Raghurajpur", "cultural", 19.8900, 85.8800, "Heritage crafts village famous for pattachitra and temples."),
    ("Puri Beach", "religious", 19.7980, 85.8280, None),
    ("Puri railway station", "cultural", 19.8131, 85.8410, None),
    ("District Headquarters Hospital Puri", "cultural", 19.8100, 85.8250, None),
    ("Lingaraj Temple viewing platform", "cultural", 20.2386, 85.8333, None),
    ("St. Mary's Church", "religious", 19.8110, 85.8300, None),
    ("Jagannath Temple, Hauz Khas", "religious", 28.5494, 77.2001, None),  # open temple outside Puri
    ("Mathematics Museum", "cultural", 20.3, 85.8, "The aftermath of the war"),
])
def test_secular_and_open_places_are_not_over_filtered(name, category, lat, lon, description) -> None:
    out = apply_rules(NON_HINDU, category, [category], [], [], poi_name=name, description=description,
                      lat=lat, lon=lon)
    assert not out.excluded, name


def test_foreign_price_missing_is_flagged_not_assumed_free() -> None:
    visitor = VisitorProfile(foreign_national=True, max_entry_fee_inr=100)
    out = apply_rules(visitor, "historical", ["historical"], [_fact("entry_fee", {"indian": 40})], [])
    assert not out.excluded and out.penalty > 0
    assert any(n.kind == "fee" and "not confirmed" in n.text for n in out.notices)


def test_verified_foreigner_restriction_excludes_foreign_nationals() -> None:
    out = apply_rules(VisitorProfile(foreign_national=True), "religious", ["religious"],
                      [_fact("foreigner_entry", {"allowed": False})], [])
    assert out.excluded and out.reason == "entry_restricted"


def test_unverified_restriction_excludes_non_hindu() -> None:
    out = apply_rules(NON_HINDU, "religious", ["religious"],
                      [_fact("non_hindu_entry", {"allowed": False, "viewing_platform": None}, "unverified")], [])
    assert out.excluded and out.reason == "entry_restricted"


def test_orthodox_puri_temple_excludes_non_hindu() -> None:
    out = apply_rules(NON_HINDU, "religious", ["religious"], [], [], poi_name="Vimala Temple", lat=19.8047, lon=85.8183)
    assert out.excluded and out.reason == "entry_restricted"


def test_secular_place_keeps_non_hindu() -> None:
    out = apply_rules(NON_HINDU, "beaches", ["beaches"], [], [], poi_name="Puri Beach", lat=19.798, lon=85.828)
    assert not out.excluded


def test_default_visitor_is_informed_but_never_excluded() -> None:
    # Religion is never assumed: everyone sees the rule as information, nobody is filtered by it.
    out = apply_rules(VisitorProfile(), "religious", ["religious"],
                      [_fact("non_hindu_entry", {"allowed": False, "viewing_platform": None})], [])
    assert not out.excluded
    assert [n.severity for n in out.notices if n.kind == "entry"] == ["info"]


@pytest.mark.parametrize(("foreign", "excluded"), [(True, True), (False, False)])
def test_fee_cap_uses_the_right_ticket_price(foreign: bool, excluded: bool) -> None:
    visitor = VisitorProfile(foreign_national=foreign, max_entry_fee_inr=200)
    out = apply_rules(visitor, "historical", ["historical"],
                      [_fact("entry_fee", {"indian": 40, "foreign": 600, "currency": "INR"})], [])
    assert out.excluded is excluded
    if excluded:
        assert out.reason == "over_budget"


def test_avoided_category_excludes() -> None:
    out = apply_rules(VisitorProfile(avoid_categories=["shopping"]), "shopping", ["shopping", "cultural"], [], [])
    assert out.excluded and out.reason == "avoided_category"


def test_verified_wheelchair_inaccessible_excludes_only_wheelchair_users() -> None:
    facts = [_fact("wheelchair_access", {"accessible": False})]
    assert apply_rules(VisitorProfile(wheelchair_user=True), "historical", [], facts, []).excluded
    assert not apply_rules(VisitorProfile(), "historical", [], facts, []).excluded


def _event(start: datetime, end: datetime) -> EventView:
    return EventView("Festival", start, end, 20.24, 85.83, 3, ("Lingaraj Temple",), "high",
                     "Big crowds.", None, "https://example.org/event", "Old Town")


def test_crowd_penalty_only_when_event_overlaps_the_visit() -> None:
    visitor = VisitorProfile(avoid_crowds=True)
    ev = _event(datetime(2026, 11, 24, 0, tzinfo=UTC), datetime(2026, 11, 24, 23, tzinfo=UTC))
    same_day = apply_rules(visitor, "religious", [], [], [ev],
                           visit_window=(datetime(2026, 11, 24, 4, tzinfo=UTC), datetime(2026, 11, 24, 8, tzinfo=UTC)))
    day_before = apply_rules(visitor, "religious", [], [], [ev],
                             visit_window=(datetime(2026, 11, 23, 4, tzinfo=UTC),
                                           datetime(2026, 11, 23, 8, tzinfo=UTC)))
    assert same_day.penalty == pytest.approx(0.35)
    assert day_before.penalty == 0
    assert any(n.kind == "crowd" for n in day_before.notices)  # still told about it
