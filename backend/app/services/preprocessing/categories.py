"""Our category taxonomy and provider-type mappings.

Two related vocabularies:
  * ``POI_CATEGORIES``  — what a place *is* (one primary + secondary list per POI)
  * ``INTEREST_CATEGORIES`` — what a user can select; most map 1:1 to POI categories,
    "photography" is an interest served by photo activity, not a place type.
"""

from __future__ import annotations

POI_CATEGORIES = (
    "nature", "adventure", "historical", "religious", "cultural", "food", "entertainment",
    "shopping", "wildlife", "beaches", "waterfalls", "parks_gardens",
)
INTEREST_CATEGORIES = (*POI_CATEGORIES, "photography")

# Google Places (New) types -> our categories (first entry = strongest signal)
GOOGLE_TYPE_MAP: dict[str, tuple[str, ...]] = {
    "zoo": ("wildlife", "nature"),
    "wildlife_park": ("wildlife", "nature"),
    "aquarium": ("wildlife", "entertainment"),
    "national_park": ("nature", "wildlife", "adventure"),
    "state_park": ("nature", "adventure"),
    "park": ("parks_gardens", "nature"),
    "botanical_garden": ("parks_gardens", "nature"),
    "garden": ("parks_gardens", "nature"),
    "hiking_area": ("adventure", "nature"),
    "beach": ("beaches", "nature"),
    "hindu_temple": ("religious", "historical"),
    "church": ("religious",),
    "mosque": ("religious",),
    "synagogue": ("religious",),
    "buddhist_temple": ("religious",),
    "place_of_worship": ("religious",),
    "historical_landmark": ("historical", "cultural"),
    "historical_place": ("historical", "cultural"),
    "monument": ("historical",),
    "museum": ("cultural", "historical"),
    "art_gallery": ("cultural",),
    "cultural_landmark": ("cultural",),
    "amusement_park": ("entertainment", "adventure"),
    "stadium": ("entertainment",),
    "movie_theater": ("entertainment",),
    "shopping_mall": ("shopping", "entertainment"),
    "market": ("shopping", "cultural"),
    "store": ("shopping",),
    "restaurant": ("food",),
    "cafe": ("food",),
    "tourist_attraction": (),  # too generic to decide a category alone
}

# Yelp category aliases -> our categories
YELP_ALIAS_MAP: dict[str, tuple[str, ...]] = {
    "zoos": ("wildlife", "nature"),
    "parks": ("parks_gardens", "nature"),
    "gardens": ("parks_gardens", "nature"),
    "hiking": ("adventure", "nature"),
    "beaches": ("beaches", "nature"),
    "landmarks": ("historical", "cultural"),
    "museums": ("cultural", "historical"),
    "artgalleries": ("cultural",),
    "religiousorgs": ("religious",),
    "hindu_temples": ("religious",),
    "buddhist_temples": ("religious",),
    "churches": ("religious",),
    "amusementparks": ("entertainment", "adventure"),
    "shoppingcenters": ("shopping",),
    "tours": ("cultural",),
    "restaurants": ("food",),
    "indpak": ("food",),
    "cafes": ("food",),
    "coffee": ("food",),
}

# Groups that are clearly *different kinds of place*; used to stop e.g. a cafe
# merging with the garden it sits in. Categories in the same group are compatible.
CATEGORY_GROUPS = {
    "outdoors": {"nature", "adventure", "wildlife", "beaches", "waterfalls", "parks_gardens"},
    "heritage": {"historical", "religious", "cultural"},
    "commerce": {"shopping"},
    "food": {"food"},
    "leisure": {"entertainment"},
}


def map_types(types: list[str] | tuple[str, ...], mapping: dict[str, tuple[str, ...]]) -> list[str]:
    seen: list[str] = []
    for t in types:
        for cat in mapping.get(t, ()):
            if cat not in seen:
                seen.append(cat)
    return seen


# Used ONLY when a provider gives no mappable type (Google often types Indian lakes, dams, hot springs
# and ghats as just "tourist_attraction"). Whole-word matches on the raw name; first hit wins.
NAME_CATEGORY_HINTS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("waterfall", "falls", "jharana", "prapat"), ("waterfalls", "nature")),
    (("beach", "sea beach"), ("beaches", "nature")),
    (("hot spring", "hot springs", "kund"), ("nature",)),
    (("lake", "sagar", "sarovar", "talab", "dam", "reservoir", "jheel"), ("nature",)),
    (("ghat",), ("religious", "cultural")),
    (("temple", "mandir", "math", "matha", "gurudwara", "mosque", "masjid", "church", "dargah", "stupa"),
     ("religious",)),
    (("fort", "qila", "garh", "caves", "gumpha", "ruins"), ("historical",)),
    (("hill", "hills", "peak", "trek"), ("nature", "adventure")),
    (("sanctuary", "national park", "reserve"), ("nature", "wildlife")),
    (("museum",), ("cultural", "historical")),
    (("park", "garden", "udyan", "kanan"), ("parks_gardens", "nature")),
)


def categories_from_name(name: str) -> list[str]:
    import re

    lowered = f" {re.sub(r'[^a-z ]+', ' ', name.lower())} "
    for words, cats in NAME_CATEGORY_HINTS:
        if any(f" {w} " in lowered for w in words):
            return list(cats)
    return []


def primary_category(categories: list[str], fallback: str = "cultural") -> str:
    return categories[0] if categories else fallback


def _groups(categories: list[str] | tuple[str, ...]) -> set[str]:
    return {g for g, members in CATEGORY_GROUPS.items() if members & set(categories)}


def category_compatibility(a: list[str] | tuple[str, ...], b: list[str] | tuple[str, ...]) -> float | None:
    """1.0 compatible, 0.0 conflicting, None when either side is unknown."""
    ga, gb = _groups(a), _groups(b)
    if not ga or not gb:
        return None
    return 1.0 if ga & gb else 0.0
