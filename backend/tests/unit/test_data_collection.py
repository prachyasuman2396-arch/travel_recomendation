"""Provider clients against httpx.MockTransport — no network, no credentials."""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.errors import ExternalServiceError, RateLimitedError
from app.services.data_collection.flickr_client import FlickrClient
from app.services.data_collection.places_client import FIELD_MASK, GooglePlacesClient, tile_circles
from app.services.data_collection.yelp_client import YelpClient

pytestmark = pytest.mark.anyio

PLACE = {"id": "ChIJ1", "displayName": {"text": "Lingaraj Temple"}, "formattedAddress": "Old Town",
         "location": {"latitude": 20.2382, "longitude": 85.8338}, "types": ["hindu_temple"],
         "rating": 4.7, "userRatingCount": 100}


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ----------------------------------------------------------------- places
async def test_places_success_sends_field_mask_and_parses():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"places": [PLACE, {"id": "bad"}, {"id": "noname",
                                                                         "location": {"latitude": 1, "longitude": 1}}]})

    async with client_for(handler) as http:
        result = await GooglePlacesClient("k", http).search_area(20.29, 85.82, 3000)
    mask = seen["headers"]["X-Goog-FieldMask"]
    assert mask.startswith(FIELD_MASK) and "places.reviews" in mask and "places.photos" in mask
    assert seen["headers"]["X-Goog-Api-Key"] == "k"
    assert seen["body"]["maxResultCount"] == 20
    assert seen["body"]["locationRestriction"]["circle"]["radius"] == 3000
    assert [r.name for r in result.records] == ["Lingaraj Temple"]
    assert result.records[0].data_origin == "live"
    assert {r.reason.split(":")[0] for r in result.rejected} == {"schema", "missing name"}


async def test_places_auth_failure_is_not_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(403, json={"error": {"status": "PERMISSION_DENIED"}})

    async with client_for(handler) as http:
        with pytest.raises(ExternalServiceError) as exc:
            await GooglePlacesClient("k", http, max_attempts=3).search_area(20.29, 85.82, 1000)
    assert exc.value.status == 403 and not exc.value.retryable
    assert calls["n"] == 1


async def test_places_rate_limit_retries_then_succeeds():
    responses = iter([httpx.Response(429, headers={"Retry-After": "1"}), httpx.Response(200, json={"places": [PLACE]})])
    async with client_for(lambda r: next(responses)) as http:
        result = await GooglePlacesClient("k", http, max_attempts=3).search_area(20.29, 85.82, 1000)
    assert len(result.records) == 1 and result.requests_made == 1


async def test_places_timeout_exhausts_retries():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ReadTimeout("slow", request=request)

    async with client_for(handler) as http:
        with pytest.raises(ExternalServiceError) as exc:
            await GooglePlacesClient("k", http, max_attempts=2).search_area(20.29, 85.82, 1000)
    assert exc.value.retryable and calls["n"] == 2


async def test_places_text_search_follows_page_tokens():
    pages = iter([{"places": [PLACE], "nextPageToken": "t2"},
                  {"places": [PLACE | {"id": "ChIJ2", "displayName": {"text": "Rajarani Temple"}}]}])
    tokens = []

    def handler(request):
        tokens.append(json.loads(request.content).get("pageToken"))
        return httpx.Response(200, json=next(pages))

    async with client_for(handler) as http:
        result = await GooglePlacesClient("k", http).search_text("temples", 20.2, 85.8, 5000, max_pages=5)
    assert tokens == [None, "t2"]
    assert len(result.records) == 2


def test_tiling_covers_large_radius_and_keeps_small_one():
    assert tile_circles(20.0, 85.0, 3000, 5000) == [(20.0, 85.0, 3000)]
    tiles = tile_circles(20.0, 85.0, 20000, 5000)
    assert len(tiles) > 4 and all(t[2] == 5000 for t in tiles)


# ----------------------------------------------------------------- flickr
FLICKR_OK = {"stat": "ok", "photos": {"page": 1, "pages": 2, "photo": [
    {"id": "1", "owner": "o1", "title": "Temple", "tags": "lingaraj temple", "latitude": "20.2382",
     "longitude": "85.8338", "accuracy": "16", "dateupload": "1600000000", "datetaken": "2020-01-01 10:00:00",
     "license": "4", "url_m": "https://live.staticflickr.com/x.jpg"},
    {"id": "2", "owner": "o2", "title": "No geo"},
]}}


async def test_flickr_parses_and_paginates():
    pages_requested = []

    def handler(request):
        pages_requested.append(request.url.params["page"])
        assert request.url.params["has_geo"] == "1"
        assert "min_upload_date" in request.url.params
        return httpx.Response(200, json=FLICKR_OK)

    async with client_for(handler) as http:
        result = await FlickrClient("k", http).search_photos(20.2, 85.8, radius_km=50, max_pages=3)
    assert pages_requested == ["1", "2"]  # stops at "pages" even though max_pages=3
    assert len(result.records) == 1 and result.records[0].tags == ("lingaraj", "temple")
    assert result.records[0].page_url == "https://www.flickr.com/photos/o1/1"
    assert len(result.rejected) == 1


async def test_flickr_error_in_200_body_is_detected():
    async with client_for(lambda r: httpx.Response(200, json={"stat": "fail", "code": 100,
                                                              "message": "Invalid API Key"})) as http:
        with pytest.raises(ExternalServiceError) as exc:
            await FlickrClient("k", http).search_photos(20.2, 85.8, 1)
    assert not exc.value.retryable and exc.value.status == 401


async def test_flickr_radius_is_capped_at_32km():
    async def check(request):
        assert float(request.url.params["radius"]) == 32.0
        return httpx.Response(200, json={"stat": "ok", "photos": {"pages": 1, "photo": []}})

    async with client_for(check) as http:
        await FlickrClient("k", http).search_photos(20.2, 85.8, radius_km=100, max_pages=1)


# ------------------------------------------------------------------- yelp
BIZ = {"id": "b1", "name": "Nandankanan Zoo", "coordinates": {"latitude": 20.39, "longitude": 85.81},
       "categories": [{"alias": "zoos", "title": "Zoos"}], "rating": 4.5, "review_count": 10,
       "location": {"display_address": ["Barang", "Odisha"]}}


async def test_yelp_search_paginates_with_offset_and_parses():
    offsets = []

    def handler(request):
        assert request.headers["Authorization"] == "Bearer k"
        offsets.append(int(request.url.params["offset"]))
        businesses = [BIZ | {"id": f"b{offsets[-1]}"}] if offsets[-1] == 0 else [BIZ | {"id": "b50"},
                                                                               {"id": "nocoords", "name": "X"}]
        return httpx.Response(200, json={"total": 60, "businesses": businesses})

    async with client_for(handler) as http:
        result = await YelpClient("k", http).search_businesses(20.3, 85.8, 5000)
    assert offsets == [0, 50]
    assert [r.external_id for r in result.records] == ["b0", "b50"]
    assert result.records[0].categories == ("zoos",)
    assert result.rejected[0].reason == "missing name or coordinates"


async def test_yelp_reviews_unavailable_on_plan_is_not_an_error():
    async with client_for(lambda r: httpx.Response(403, json={"error": {"code": "NOT_AUTHORIZED"}})) as http:
        result = await YelpClient("k", http).get_reviews("b1")
    assert result.records == [] and "not available" in result.notes[0]


async def test_yelp_daily_quota_is_not_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, json={"error": {"code": "ACCESS_LIMIT_REACHED"}})

    async with client_for(handler) as http:
        with pytest.raises(ExternalServiceError) as exc:
            await YelpClient("k", http, max_attempts=3).search_businesses(20.3, 85.8, 5000)
    assert calls["n"] == 1 and not exc.value.retryable


async def test_yelp_per_second_throttle_is_retried():
    responses = iter([httpx.Response(429, json={"error": {"code": "TOO_MANY_REQUESTS_PER_SECOND"}}),
                      httpx.Response(200, json={"total": 1, "businesses": [BIZ]})])
    async with client_for(lambda r: next(responses)) as http:
        result = await YelpClient("k", http, max_attempts=3).search_businesses(20.3, 85.8, 5000)
    assert len(result.records) == 1


async def test_yelp_reviews_parsed():
    payload = {"reviews": [{"id": "r1", "rating": 5, "text": "Lovely", "time_created": "2025-01-01 10:00:00"}]}
    async with client_for(lambda r: httpx.Response(200, json=payload)) as http:
        result = await YelpClient("k", http).get_reviews("b1")
    assert result.records[0].text == "Lovely" and result.records[0].created_at.year == 2025


def test_missing_key_is_rejected_at_construction():
    with pytest.raises(ValueError):
        GooglePlacesClient("", httpx.AsyncClient())


def test_rate_limit_error_is_retryable():
    assert RateLimitedError("x", provider="p").retryable


async def test_places_reviews_photos_accessibility_parsed_and_reviews_optional():
    place = PLACE | {
        "reviews": [{"name": "places/ChIJ1/reviews/a", "rating": 5, "text": {"text": "Shant aur sundar",
                                                                            "languageCode": "hi-Latn"},
                     "publishTime": "2026-10-01T05:00:00Z", "authorAttribution": {"displayName": "Asha",
                                                                                  "uri": "https://x"}}],
        "photos": [{"name": "places/ChIJ1/photos/p1", "widthPx": 800, "authorAttributions": [{"displayName": "R"}]}],
        "accessibilityOptions": {"wheelchairAccessibleEntrance": False}, "goodForChildren": True,
    }
    masks = []

    def handler(request):
        masks.append(request.headers["X-Goog-FieldMask"])
        return httpx.Response(200, json={"places": [place]})

    async with client_for(handler) as http:
        rec = (await GooglePlacesClient("k", http).search_area(20.29, 85.82, 1000)).records[0]
        await GooglePlacesClient("k", http, include_reviews=False).search_area(20.29, 85.82, 1000)
    assert rec.reviews[0].author_name == "Asha" and rec.reviews[0].language == "hi-Latn"
    assert rec.reviews[0].published_at.year == 2026
    assert rec.photos[0].name == "places/ChIJ1/photos/p1" and rec.photos[0].attributions == ("R",)
    assert rec.wheelchair_accessible is False and rec.good_for_children is True
    assert "places.reviews" not in masks[1]  # Enterprise+Atmosphere fields are opt-out to save cost


async def test_places_photo_uri_never_exposes_key_to_caller():
    def handler(request):
        assert request.url.path.endswith("/photos/p1/media") and request.url.params["skipHttpRedirect"] == "true"
        return httpx.Response(200, json={"photoUri": "https://lh3.googleusercontent.com/abc"})

    async with client_for(handler) as http:
        uri = await GooglePlacesClient("k", http).get_photo_uri("places/ChIJ1/photos/p1")
        bad = await GooglePlacesClient("k", http).get_photo_uri("../../etc/passwd")
    assert uri == "https://lh3.googleusercontent.com/abc" and "k" not in uri.split("/")[-1] and bad is None
