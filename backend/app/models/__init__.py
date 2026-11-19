"""ORM models. Importing this package registers every table on ``Base.metadata``."""

from app.models.entities import (
    Event,
    FlickrPhoto,
    IngestionRun,
    MatchCandidate,
    Poi,
    PoiContext,
    PoiDocument,
    PoiEmbedding,
    PoiFact,
    PoiRating,
    PoiSentiment,
    PoiSource,
    Review,
    ReviewAnalysis,
    User,
    YelpBusiness,
)

__all__ = [
    "Event",
    "FlickrPhoto",
    "IngestionRun",
    "MatchCandidate",
    "Poi",
    "PoiContext",
    "PoiDocument",
    "PoiEmbedding",
    "PoiFact",
    "PoiRating",
    "PoiSentiment",
    "PoiSource",
    "Review",
    "ReviewAnalysis",
    "User",
    "YelpBusiness",
]
