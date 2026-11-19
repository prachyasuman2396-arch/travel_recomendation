"""Wikipedia GeoSearch hits -> visitable places. Cases are real descriptions returned for Puri and Bhubaneswar."""

from __future__ import annotations

import httpx
import pytest

from app.lite.wiki_places import categories_for, is_visitable
from app.services.data_collection.wikipedia_client import WikipediaClient


@pytest.mark.parametrize(("title", "description", "coord_type"), [
    ("Jagannath Temple, Puri", "Hindu temple at Puri, Odisha, India", "landmark"),
    ("Puri Beach", "Beach in Puri, India", "landmark"),
    ("Govardhan Math", "Religious institution in Hinduism", None),
    ("Aruna Stambha", "Religious Pillar of Jagannath Temple at Puri, Odisha, India", None),
    ("Atharanala", "Bridge in Puri town, Odisha (India)", None),
    ("Shree Jagannath Heritage Corridor, Puri", "Hindu temple in Odisha, India", None),
    ("Mukti Mandapa", None, None),
    ("Odisha State Museum", "Museum in Bhubaneswar, India", None),
    ("Tribal Research Institute Museum", "Museum in Bhubaneswar, India", None),
    ("Pathani Samanta Planetarium", "Planetarium", None),
    ("Ekamra Kanan", "Botanical garden in Bhubaneswar, Odisha, India", None),
    ("Forum Mart", "Shopping mall in Odisha, India", None),
    ("Rameshwar Deula", "Temple in Odisha, India", None),
])
def test_attractions_are_kept(title, description, coord_type) -> None:
    assert is_visitable(title, description, coord_type), title


@pytest.mark.parametrize(("title", "description", "coord_type"), [
    ("Puri (city)", "City in Odisha, India", None),
    ("Bring Your Own Film Festival", "Film festival in Puri, India", None),
    ("Puri district", "District of Odisha in India", None),
    ("Puri Lok Sabha constituency", "Lok Sabha constituency in Odisha", None),
    ("Puri Assembly constituency", "Constituency of the Odisha legislative assembly in India", None),
    ("Puri Zilla School", "School in Puri, Odisha, India", None),
    ("Lok Bhavan, Puri", "2nd Residence of the Governor of Odisha", None),
    ("Shri Jagannath Medical College and Hospital", "8th Medical College in Odisha located at Puri", None),
    ("Malatipatpur railway station", "Railway station in Odisha", "railwaystation"),
    ("Puri International Airport", "Future airport to serve Puri, Odisha, India", None),
    ("Alipada", "Village in Odisha, India", None),
    ("Odisha", "State in Eastern India", "adm1st"),
    ("Utkal University", "Public university in Bhubaneswar, Odisha, India", "edu"),
    ("Jajpur derailment", "2009 railway incident in Odisha, India", "event"),
    ("Saheed Nagar", "Neighbourhood in Bhubaneswar, Odisha", None),
    ("Salia Sahi", "Largest slum of Odisha, India", None),
    ("Mancheswar Industrial Estate", "Industrial and commercial area located", None),
    ("Radio Choklate", "Radio station in Bhubaneswar, India", None),
    ("Chari Kshetra", "Four holy regions in Odisha", None),
])
def test_non_places_are_dropped(title, description, coord_type) -> None:
    assert not is_visitable(title, description, coord_type), title


@pytest.mark.parametrize(("title", "description", "first"), [
    ("Ekamra Kanan", "Botanical garden in Bhubaneswar", "parks_gardens"),
    ("Forum Mart", "Shopping mall in Odisha, India", "shopping"),
    ("Pathani Samanta Planetarium", "Planetarium", "cultural"),
    ("Aruna Stambha", "Religious Pillar of Jagannath Temple at Puri", "historical"),  # a pillar, not the temple
    ("Rameshwar Deula", "Temple in Odisha, India", "religious"),
    ("Atharanala", "Bridge in Puri town, Odisha (India)", "historical"),
    ("Vimala Temple", "Hindu temple in Puri, India", "religious"),
])
def test_categories_use_the_description(title, description, first) -> None:
    assert categories_for(title, description)[0] == first


@pytest.mark.anyio
async def test_geosearch_returns_descriptions_nearest_first() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"query": {"pages": [
            {"pageid": 2, "title": "Far Temple", "description": "Hindu temple",
             "coordinates": [{"lat": 19.85, "lon": 85.85, "type": "landmark"}]},
            {"pageid": 1, "title": "Puri Beach", "description": "Beach in Puri, India",
             "coordinates": [{"lat": 19.798, "lon": 85.826}]},
            {"pageid": 3, "title": "No Coordinates"},
        ]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        pages = await WikipediaClient(http, "ua", max_attempts=1).geosearch(19.7982, 85.8249, 25_000, limit=100)
    assert [p["title"] for p in pages] == ["Puri Beach", "Far Temple"]
    assert pages[1]["type"] == "landmark" and pages[0]["description"] == "Beach in Puri, India"
    assert seen["generator"] == "geosearch" and seen["ggsradius"] == "10000" and "description" in seen["prop"]


@pytest.mark.anyio
async def test_geosearch_failure_degrades_to_empty() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as http:
        assert await WikipediaClient(http, "ua", max_attempts=1).geosearch(19.8, 85.8, 5000) == []
