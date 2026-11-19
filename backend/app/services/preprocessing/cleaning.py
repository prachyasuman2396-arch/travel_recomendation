"""Reusable cleaning/validation functions. Pure functions: no I/O, easy to test."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections.abc import Callable, Iterable
from typing import TypeVar

from app.services.data_collection.base import (
    FlickrPhotoRecord,
    PlaceRecord,
    Rejection,
    YelpBusinessRecord,
    YelpReviewRecord,
)

T = TypeVar("T")

class _PunctPattern:
    """Replace punctuation/symbols (Unicode categories P*, S*) with spaces.

    Not a ``[^\\w\\s]`` regex: ``\\w`` excludes combining marks, which would shred Indic scripts
    (Odia/Devanagari vowel signs are combining marks)."""

    @staticmethod
    def sub(repl: str, text: str) -> str:
        return "".join(repl if unicodedata.category(c)[0] in "PS" else c for c in text)


_PUNCT = _PunctPattern()
_SPACES = re.compile(r"\s+")
ADDRESS_ABBREVIATIONS = {
    "rd": "road", "st": "street", "sq": "square", "nr": "near", "opp": "opposite",
    "ave": "avenue", "ln": "lane", "bbsr": "bhubaneswar", "dist": "district",
    # Indian address vocabulary
    "marg": "road", "path": "road", "chhak": "square", "chowk": "square", "chauraha": "square",
    "dist.": "district", "po": "post", "ps": "police", "bhubaneshwar": "bhubaneswar", "orissa": "odisha",
}
NAME_STOPWORDS = {"the", "of", "and", "a"}
# Honorifics that providers include inconsistently ("Shree Jagannath Temple" vs "Jagannath Temple")
NAME_HONORIFICS = {"sri", "shri", "shree", "sree", "srii", "maa", "ma", "bhagwan", "lord", "sant"}
# Hindi/Odia/Sanskrit place words -> one canonical English token, so names compare across providers.
INDIAN_NAME_SYNONYMS = {
    "mandir": "temple", "mandira": "temple", "devalaya": "temple", "deula": "temple", "devasthanam": "temple",
    "sagar": "lake", "sagara": "lake", "talab": "lake", "talao": "lake", "jheel": "lake", "pokhari": "lake",
    "sarovar": "lake", "kund": "tank", "kunda": "tank",
    "gumpha": "caves", "gufa": "caves", "guha": "caves", "cave": "caves",
    "giri": "hill", "pahad": "hill", "pahar": "hill", "parbat": "hill", "parvat": "hill",
    "kanan": "garden", "udyan": "garden", "udyana": "garden", "bagicha": "garden", "vatika": "garden",
    "bagh": "garden", "gardens": "garden",
    "jharana": "waterfall", "jharna": "waterfall", "prapat": "waterfall", "falls": "waterfall",
    "shanti": "peace", "pagoda": "stupa", "smarak": "memorial", "sangrahalaya": "museum",
    "qila": "fort", "kila": "fort", "garh": "fort", "durg": "fort",
}
DROP_COUNTRY = {"india"}


def normalize_text(value: str | None) -> str | None:
    """NFKC, trim, collapse whitespace. Empty -> None."""
    if value is None:
        return None
    text = _SPACES.sub(" ", unicodedata.normalize("NFKC", value)).strip()
    return text or None


def _fold(value: str) -> str:
    """Strip accents from Latin text only. Indic scripts (Odia, Devanagari...) keep their vowel signs:
    removing those combining marks would corrupt the word rather than normalise it."""
    decomposed = unicodedata.normalize("NFKD", value)
    out = []
    for c in decomposed:
        if unicodedata.combining(c) and out and ord(out[-1]) < 0x250:
            continue
        out.append(c)
    return unicodedata.normalize("NFC", "".join(out))


def normalize_name(value: str | None, *, canonical: bool = True) -> str:
    """Lowercase, accent-fold, '&'->'and', drop punctuation, stopwords and honorifics, and (when
    ``canonical``) map Indian place words to canonical tokens (mandir -> temple, sagar -> lake).
    Use ``canonical=False`` to compare against text written in local spelling (e.g. photo tags)."""
    if not value:
        return ""
    text = _fold(value.lower()).replace("&", " and ")
    text = _PUNCT.sub(" ", text)
    tokens = [t for t in text.split() if t not in NAME_STOPWORDS and t not in NAME_HONORIFICS]
    if canonical:
        tokens = [INDIAN_NAME_SYNONYMS.get(t, t) for t in tokens]
    return " ".join(tokens)


_PIN = re.compile(r"\b([1-9]\d{2}) ?(\d{3})\b")  # "751002" or "751 002"


def extract_pin(address: str | None) -> str | None:
    """Indian 6-digit PIN code, if present."""
    if not address:
        return None
    m = _PIN.search(address)
    return m.group(1) + m.group(2) if m else None


def normalize_address(value: str | None) -> str | None:
    if not value:
        return None
    text = _PUNCT.sub(" ", _fold(value.lower()))
    tokens = [ADDRESS_ABBREVIATIONS.get(t, t) for t in text.split() if t not in DROP_COUNTRY]
    return " ".join(tokens) or None


def valid_coordinates(lat: float | None, lon: float | None) -> bool:
    if lat is None or lon is None:
        return False
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return False
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return False
    return not (abs(lat) < 1e-9 and abs(lon) < 1e-9)  # "null island" = missing data


def normalize_rating(value: float | None, scale_max: float = 5.0) -> float | None:
    """Return rating on a 0–5 scale or None if invalid. Never clamps silently."""
    if value is None:
        return None
    try:
        rating = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(rating) or rating < 0 or rating > scale_max:
        return None
    return round(rating * 5.0 / scale_max, 2)


def text_hash(text: str | None) -> str | None:
    if not text:
        return None
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def dedupe(records: Iterable[T], key: Callable[[T], str]) -> tuple[list[T], int]:
    """Keep the first occurrence of each key; return (unique, duplicates_dropped)."""
    seen: set[str] = set()
    unique: list[T] = []
    dropped = 0
    for record in records:
        k = key(record)
        if k in seen:
            dropped += 1
            continue
        seen.add(k)
        unique.append(record)
    return unique, dropped


def clean_places(records: list[PlaceRecord]) -> tuple[list[PlaceRecord], list[Rejection]]:
    rejected: list[Rejection] = []
    clean: list[PlaceRecord] = []
    unique, _ = dedupe(records, lambda r: r.external_id)
    for r in unique:
        name = normalize_text(r.name)
        if not name:
            rejected.append(Rejection(provider=r.provider, external_id=r.external_id, reason="empty name"))
        elif not valid_coordinates(r.latitude, r.longitude):
            rejected.append(Rejection(provider=r.provider, external_id=r.external_id, reason="invalid coordinates"))
        elif r.business_status == "CLOSED_PERMANENTLY":
            rejected.append(Rejection(provider=r.provider, external_id=r.external_id, reason="permanently closed"))
        else:
            clean.append(r.model_copy(update={
                "name": name,
                "address": normalize_text(r.address),
                "description": normalize_text(r.description),
                "rating": normalize_rating(r.rating),
                "rating_count": r.rating_count if (r.rating_count or 0) >= 0 else None,
            }))
    return clean, rejected


def clean_yelp_businesses(records: list[YelpBusinessRecord]) -> tuple[list[YelpBusinessRecord], list[Rejection]]:
    rejected: list[Rejection] = []
    clean: list[YelpBusinessRecord] = []
    unique, _ = dedupe(records, lambda r: r.external_id)
    for r in unique:
        name = normalize_text(r.name)
        if not name:
            rejected.append(Rejection(provider="yelp", external_id=r.external_id, reason="empty name"))
        elif not valid_coordinates(r.latitude, r.longitude):
            rejected.append(Rejection(provider="yelp", external_id=r.external_id, reason="invalid coordinates"))
        elif r.is_closed:
            rejected.append(Rejection(provider="yelp", external_id=r.external_id, reason="closed"))
        else:
            clean.append(r.model_copy(update={"name": name, "rating": normalize_rating(r.rating),
                                              "address": normalize_text(r.address)}))
    return clean, rejected


def clean_reviews(records: list[YelpReviewRecord]) -> tuple[list[YelpReviewRecord], list[Rejection]]:
    rejected: list[Rejection] = []
    clean: list[YelpReviewRecord] = []
    unique, _ = dedupe(records, lambda r: r.external_id)
    for r in unique:
        text = normalize_text(r.text)
        rating = normalize_rating(r.rating)
        if not text and rating is None:
            rejected.append(Rejection(provider="yelp", external_id=r.external_id, reason="no text and no rating"))
            continue
        clean.append(r.model_copy(update={"text": text, "rating": rating}))
    return clean, rejected


def clean_photos(records: list[FlickrPhotoRecord], min_accuracy: int = 11
                 ) -> tuple[list[FlickrPhotoRecord], list[Rejection]]:
    """Drop photos with unusable geo data. Flickr accuracy: 1=world … 16=street."""
    rejected: list[Rejection] = []
    clean: list[FlickrPhotoRecord] = []
    unique, _ = dedupe(records, lambda r: r.external_id)
    for r in unique:
        if not valid_coordinates(r.latitude, r.longitude):
            rejected.append(Rejection(provider="flickr", external_id=r.external_id, reason="invalid coordinates"))
        elif r.accuracy is not None and r.accuracy < min_accuracy:
            rejected.append(Rejection(provider="flickr", external_id=r.external_id, reason="low geo accuracy"))
        else:
            clean.append(r.model_copy(update={
                "title": normalize_text(r.title),
                "tags": tuple(sorted({normalize_name(t) for t in r.tags if normalize_name(t)})),
            }))
    return clean, rejected
