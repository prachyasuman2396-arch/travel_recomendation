"""Normalised source records + collector interfaces.

Every provider client converts its own response shape into these records, so
nothing downstream (cleaning, matching, storage) knows about provider JSON.
The three clients are independent: none imports another.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Generic, Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field

DataOrigin = Literal["mock", "live"]


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True)
    data_origin: DataOrigin
    fetched_at: datetime


class PlaceReview(BaseModel):
    model_config = ConfigDict(frozen=True)
    external_id: str
    rating: float | None = None
    text: str | None = None
    language: str | None = None
    published_at: datetime | None = None
    author_name: str | None = None
    author_url: str | None = None


class PlacePhoto(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str  # Google photo resource name; resolved to a short-lived URL on demand, never cached long-term
    width: int | None = None
    height: int | None = None
    attributions: tuple[str, ...] = ()


class PlaceRecord(_Record):
    provider: Literal["google_places"] = "google_places"
    external_id: str
    name: str
    address: str | None = None
    latitude: float
    longitude: float
    types: tuple[str, ...] = ()
    primary_type: str | None = None
    description: str | None = None
    rating: float | None = None
    rating_count: int | None = None
    opening_hours: dict | None = None
    website: str | None = None
    maps_url: str | None = None
    business_status: str | None = None
    reviews: tuple[PlaceReview, ...] = ()
    photos: tuple[PlacePhoto, ...] = ()
    wheelchair_accessible: bool | None = None
    good_for_children: bool | None = None


class FlickrPhotoRecord(_Record):
    provider: Literal["flickr"] = "flickr"
    external_id: str
    owner_id: str | None = None
    owner_name: str | None = None
    title: str | None = None
    tags: tuple[str, ...] = ()
    latitude: float
    longitude: float
    accuracy: int | None = None
    uploaded_at: datetime | None = None
    taken_at: datetime | None = None
    license_id: str | None = None
    photo_url: str | None = None
    page_url: str | None = None


class YelpBusinessRecord(_Record):
    provider: Literal["yelp"] = "yelp"
    external_id: str
    name: str
    address: str | None = None
    latitude: float
    longitude: float
    categories: tuple[str, ...] = ()  # Yelp category aliases, e.g. "parks"
    rating: float | None = None
    review_count: int | None = None
    url: str | None = None
    is_closed: bool = False


class YelpReviewRecord(_Record):
    provider: Literal["yelp"] = "yelp"
    external_id: str
    business_external_id: str
    rating: float | None = None
    text: str | None = None
    created_at: datetime | None = None


class OsmPlaceRecord(_Record):
    """An OpenStreetMap feature (node/way/relation) with the tags we use."""

    provider: Literal["osm"] = "osm"
    external_id: str  # e.g. "node/123", "way/456"
    name: str
    latitude: float
    longitude: float
    tags: dict[str, str] = Field(default_factory=dict)
    alt_names: tuple[str, ...] = ()
    wikipedia: str | None = None  # "en:Title"
    wikidata: str | None = None


class WikiPageRecord(_Record):
    provider: Literal["wikipedia"] = "wikipedia"
    title: str
    url: str
    summary: str | None = None
    text: str
    license: str = "CC BY-SA 4.0"
    image_url: str | None = None
    image_attribution: str | None = None


class Rejection(BaseModel):
    provider: str
    external_id: str | None = None
    reason: str


T = TypeVar("T", bound=BaseModel)


@dataclass
class CollectionResult(Generic[T]):
    records: list[T] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)
    raw_pages: list[dict] = field(default_factory=list)  # passed to the raw store when permitted
    requests_made: int = 0
    notes: list[str] = field(default_factory=list)


class PlacesCollector(Protocol):
    data_origin: DataOrigin

    async def search_area(
        self, latitude: float, longitude: float, radius_m: float, included_types: list[str] | None = None
    ) -> CollectionResult[PlaceRecord]: ...


class FlickrCollector(Protocol):
    data_origin: DataOrigin

    async def search_photos(
        self, latitude: float, longitude: float, radius_km: float, max_pages: int = 2
    ) -> CollectionResult[FlickrPhotoRecord]: ...


class OsmCollector(Protocol):
    data_origin: DataOrigin

    async def search_area(
        self, latitude: float, longitude: float, radius_m: float
    ) -> CollectionResult[OsmPlaceRecord]: ...


class WikipediaCollector(Protocol):
    data_origin: DataOrigin

    async def get_page(self, title: str | None = None, wikidata: str | None = None) -> WikiPageRecord | None: ...


class YelpCollector(Protocol):
    data_origin: DataOrigin

    async def search_businesses(
        self, latitude: float, longitude: float, radius_m: int, categories: list[str] | None = None
    ) -> CollectionResult[YelpBusinessRecord]: ...

    async def get_reviews(self, business_id: str) -> CollectionResult[YelpReviewRecord]: ...
