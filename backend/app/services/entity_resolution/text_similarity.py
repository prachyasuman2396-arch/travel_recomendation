"""String similarity built on RapidFuzz. Returns scores in [0, 1]."""

from __future__ import annotations

from rapidfuzz import fuzz


def name_similarity(a: str, b: str) -> float:
    """Blend of token-set (handles 'Nandankanan Zoo' ⊂ 'Nandankanan Zoological Park')
    and token-sort (penalises extra words such as 'Cafe')."""
    if not a or not b:
        return 0.0
    return (0.5 * fuzz.token_set_ratio(a, b) + 0.5 * fuzz.token_sort_ratio(a, b)) / 100.0


def address_similarity(a: str | None, b: str | None) -> float | None:
    """``None`` means 'not comparable' rather than 'different'.

    Addresses with fewer than 3 tokens (e.g. just "bhubaneswar odisha") are
    treated as not comparable: as a token subset of every local address they
    would otherwise score a misleading 1.0.
    """
    if not a or not b or min(len(a.split()), len(b.split())) < 3:
        return None
    return fuzz.token_set_ratio(a, b) / 100.0


def token_overlap(text: str, keywords: set[str]) -> float:
    """Share of ``keywords`` that appear (fuzzily) in ``text`` tokens."""
    if not keywords:
        return 0.0
    tokens = set(text.split())
    hits = 0
    for kw in keywords:
        if kw in tokens or any(len(t) >= 4 and fuzz.ratio(kw, t) >= 88 for t in tokens):
            hits += 1
    return hits / len(keywords)


# Words that describe the *kind* or *region* of a place rather than which place it is.
GENERIC_NAME_TOKENS = {
    "temple", "museum", "lake", "tank", "garden", "park", "caves", "hill", "fort", "waterfall", "beach",
    "stupa", "memorial", "zoo", "zoological", "botanical", "sanctuary", "wildlife", "national", "dam",
    "reservoir", "complex", "mall", "market", "haat", "bazaar", "square", "road", "centre", "center",
    "regional", "city", "odisha", "orissa", "bhubaneswar", "puri", "cuttack", "india", "indian", "new", "old",
    "ghat", "river", "sea", "point", "view", "tower", "hall", "gallery", "monument", "site", "ruins",
}


def distinctive_tokens(normalized_name: str) -> set[str]:
    return {t for t in normalized_name.split() if t not in GENERIC_NAME_TOKENS and len(t) > 1}


def distinctive_conflict(a: str, b: str) -> bool:
    """True when both names have distinctive words and none of them correspond.

    'odisha crafts museum' vs 'odisha state museum' -> {crafts} vs {state} -> conflict (two museums).
    'lingaraja temple' vs 'lingaraj temple' -> fuzzy match -> no conflict.
    """
    da, db = distinctive_tokens(a), distinctive_tokens(b)
    if not da or not db:
        return False
    return not any(x == y or (len(x) >= 4 and len(y) >= 4 and fuzz.ratio(x, y) >= 85) for x in da for y in db)


def compact_similarity(raw_a: str, raw_b: str) -> float:
    """Similarity of the distinctive words with spaces removed, for Indian compound spellings:
    'bindusagar' vs 'bindu sagar lake' -> 'bindusagar' == 'bindusagar'; 'rajarani' vs 'raja rani'.
    Inputs are un-canonicalised normalised names. Generic words are dropped first, so
    'ekamra haat handloom expo' does not collapse onto 'ekamra haat'. Two variants are tried
    (English generic words only / plus local ones) because local words like 'sagar' are often
    part of the proper name ('Bindusagar')."""
    best = 0.0
    for generic in (GENERIC_NAME_TOKENS, GENERIC_NAME_TOKENS | _GENERIC_LOCAL):
        a = "".join(t for t in raw_a.split() if t not in generic)
        b = "".join(t for t in raw_b.split() if t not in generic)
        if len(a) >= 6 and len(b) >= 6:
            best = max(best, fuzz.ratio(a, b) / 100.0)
    return best


# Local-language generic place words (un-canonicalised forms of the synonyms in cleaning.py)
_GENERIC_LOCAL = {"mandir", "mandira", "sagar", "talab", "jheel", "pokhari", "kanan", "udyan", "gumpha",
                  "giri", "pahad", "jharana", "prapat", "falls", "gardens", "caves", "temples"}
