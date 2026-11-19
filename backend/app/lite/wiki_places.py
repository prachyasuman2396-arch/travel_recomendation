"""Turn Wikipedia GeoSearch hits into tourist places — and drop the hits that are not places to visit.

GeoSearch returns *every* geotagged article: the city itself, its district, election constituencies,
schools, hospitals, railway stations, festivals, slums, industrial estates. Live mode used to label all of
them "historical" and recommend them ("Puri Assembly constituency", "Bring Your Own Film Festival").

The decision uses Wikipedia's own short description (one batched request, e.g. "Hindu temple in Puri",
"Village in Odisha, India", "Lok Sabha constituency in Odisha") plus the title:

0. an unmistakable attraction word (temple, museum, beach, fort, …) always keeps the page;
1. a STRONG negative (constituency, school, hospital, railway station, …) always drops the page;
2. otherwise an attraction word (temple, beach, museum, fort, mall, bridge, …) keeps it;
3. otherwise a WEAK negative (city, village, neighbourhood, residence, festival, …) drops it;
4. otherwise (no description, nothing recognisable) it is kept — unknown is not evidence of "not a place".
"""

from __future__ import annotations

import re

from app.services.preprocessing.categories import categories_from_name


def _words(*terms: str) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(re.escape(t) for t in terms) + r")\b", re.IGNORECASE)


_STRONG_NEGATIVE = _words(
    "constituency", "constituencies", "legislative assembly", "lok sabha", "vidhan sabha", "election",
    "school", "college", "university", "institute", "institution of higher", "research", "educational",
    "hospital", "medical", "railway station", "metro station", "bus station", "radio station", "airport",
    "derailment", "incident", "accident", "disaster", "slum", "industrial", "industrial estate",
    "district", "tehsil", "subdivision", "municipal corporation", "municipality", "police", "court",
    "government organisation", "government organization", "company", "newspaper", "television",
)
# Unmistakable attractions win even over strong negatives ("Tribal Research Institute Museum").
_DEFINITE_ATTRACTION = _words("temple", "mandir", "museum", "beach", "fort", "palace", "waterfall", "zoo",
                              "sanctuary", "national park", "monument", "planetarium", "botanical garden")
_ATTRACTION = _words(
    "temple", "mandir", "mandira", "deula", "shrine", "math", "matha", "monastery", "ashram", "religious",
    "church", "cathedral", "basilica", "mosque", "masjid", "dargah", "gurdwara", "gurudwara", "stupa",
    "monument", "memorial", "pillar", "stambha", "fort", "fortress", "palace", "tomb", "mausoleum", "ruins",
    "cave", "caves", "heritage", "archaeological", "museum", "planetarium", "gallery", "aquarium",
    "beach", "lake", "lagoon", "river", "waterfall", "falls", "hill", "hills", "dam", "garden", "gardens",
    "park", "zoo", "sanctuary", "national park", "reserve", "botanical", "bridge", "ghat", "tank",
    "shopping mall", "mall", "market", "bazaar", "stadium", "viewpoint", "lighthouse", "tourist",
    "protected area", "wildlife", "forest", "mangrove",
)
_WEAK_NEGATIVE = _words(
    "city", "town", "village", "census town", "suburb", "neighbourhood", "neighborhood", "locality",
    "area", "state", "capital", "region", "regions", "residence", "festival", "film", "commercial", "organisation",
    "organization", "society", "library", "office", "headquarters", "building",
)
_TITLE_DISAMBIG = re.compile(r"\((?:city|town|village|district|film|band|newspaper|constituency)\)$", re.I)
_NON_PLACE_COORD_TYPES = {"country", "adm1st", "adm2nd", "adm3rd", "city", "edu", "event", "railwaystation",
                          "airport"}


def is_visitable(title: str, description: str | None, coord_type: str | None = None) -> bool:
    text = f"{title} {description or ''}"
    if _DEFINITE_ATTRACTION.search(title) or _DEFINITE_ATTRACTION.search(description or ""):
        return True
    if _TITLE_DISAMBIG.search(title.strip()) or _STRONG_NEGATIVE.search(text):
        return False
    if _ATTRACTION.search(text):
        return True
    if (coord_type or "").lower() in _NON_PLACE_COORD_TYPES:
        return False
    return not _WEAK_NEGATIVE.search(text)


def categories_for(title: str, description: str | None) -> list[str]:
    """Categories from the title first, then the kind of place the description names.

    The description's own kind wins over places it merely mentions: "Religious pillar of Jagannath Temple"
    is a monument (open to all, seen from the street), not a temple — so it must not inherit "religious",
    which would also wrongly hide it from non-Hindu visitors.
    """
    if cats := categories_from_name(title):
        return cats
    text = f"{title} {description or ''}".lower()
    if re.search(r"\b(?:mall|market|bazaar)\b", text):
        return ["shopping"]
    if re.search(r"\b(?:museum|planetarium|gallery|aquarium)\b", text):
        return ["cultural", "historical"]
    if re.search(r"\b(?:monument|memorial|pillar|stambha|palace|tomb|heritage|bridge|archaeological)\b", text):
        return ["historical", "cultural"]
    if re.search(r"\b(?:stadium|theatre|theater|amusement)\b", text):
        return ["entertainment"]
    if cats := categories_from_name(description or ""):
        return cats
    if re.search(r"\b(?:religious|hindu|monastery|holy|sacred|pilgrim)\b", text):
        return ["religious"]
    return ["cultural"]


def osm_tags_for(categories: list[str]) -> dict[str, str]:
    """OSM-style tags so the shared ingestion pipeline derives the same categories as for OSM places."""
    primary = categories[0] if categories else "cultural"
    return {
        "religious": {"historic": "temple", "amenity": "place_of_worship"},
        "beaches": {"natural": "beach"},
        "waterfalls": {"natural": "waterfall"},
        "parks_gardens": {"leisure": "park"},
        "nature": {"leisure": "nature_reserve"} if "wildlife" in categories else {"tourism": "viewpoint"},
        "wildlife": {"tourism": "zoo"},
        "historical": {"historic": "monument"},
        "shopping": {"shop": "mall"},
        "entertainment": {"tourism": "theme_park"},
    }.get(primary, {"tourism": "museum"})
