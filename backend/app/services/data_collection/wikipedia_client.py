"""Wikipedia / Wikidata enrichment: proper descriptions and source text for fact extraction.

* Wikidata entity JSON -> English sitelink title + main image (P18) from Wikimedia Commons
* Wikipedia REST summary  -> short description
* MediaWiki TextExtracts  -> plain-text article body (stored in poi_documents for the fact agent)

Content is CC BY-SA 4.0: the UI shows "From Wikipedia" with a link; images come from Commons with
attribution. Wikimedia asks for a descriptive User-Agent with contact details (CONTACT_EMAIL).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import httpx

from app.core.errors import ExternalServiceError
from app.core.http import send
from app.core.retry import with_retries
from app.services.data_collection.base import WikiPageRecord

logger = logging.getLogger(__name__)
PROVIDER = "wikipedia"
MAX_TEXT_CHARS = 20_000


def parse_wikipedia_tag(tag: str | None, default_lang: str = "en") -> tuple[str, str] | None:
    """OSM 'wikipedia' tag 'en:Lingaraja Temple' -> ('en', 'Lingaraja Temple')."""
    if not tag:
        return None
    lang, _, title = tag.partition(":")
    if not title:
        return default_lang, lang
    return lang, title


class WikipediaClient:
    data_origin = "live"

    def __init__(self, http: httpx.AsyncClient, user_agent: str, language: str = "en", *, max_attempts: int = 3):
        self._http = http
        self._ua = user_agent
        self._lang = language
        self._attempts = max_attempts

    async def _get(self, url: str, params: dict | None = None) -> dict:
        async def call() -> dict:
            request = self._http.build_request("GET", url, params=params,
                                               headers={"User-Agent": self._ua, "Accept": "application/json"})
            return (await send(self._http, request, PROVIDER)).json()

        return await with_retries(call, attempts=self._attempts, operation="wikipedia")

    async def _title_from_wikidata(self, qid: str) -> tuple[str | None, str | None]:
        data = await self._get(f"https://www.wikidata.org/wiki/Special:EntityData/{quote(qid)}.json")
        entity = (data.get("entities") or {}).get(qid) or {}
        title = ((entity.get("sitelinks") or {}).get(f"{self._lang}wiki") or {}).get("title")
        image = None
        for claim in (entity.get("claims") or {}).get("P18", [])[:1]:
            image = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        return title, image

    async def get_page(self, title: str | None = None, wikidata: str | None = None) -> WikiPageRecord | None:
        image_file = None
        if not title and wikidata:
            title, image_file = await self._title_from_wikidata(wikidata)
        if not title:
            return None
        base = f"https://{self._lang}.wikipedia.org"
        try:
            summary = await self._get(f"{base}/api/rest_v1/page/summary/{quote(title.replace(' ', '_'))}")
        except ExternalServiceError as exc:
            if exc.status == 404:
                return None
            raise
        extract = await self._get(f"{base}/w/api.php", params={
            "action": "query", "prop": "extracts", "explaintext": 1, "titles": title, "format": "json",
            "redirects": 1,
        })
        pages = ((extract.get("query") or {}).get("pages") or {}).values()
        text = next((p.get("extract") for p in pages if p.get("extract")), None) or summary.get("extract") or ""
        image_url = None
        attribution = None
        if image_file:
            image_url = f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(image_file)}?width=1200"
            attribution = f"{image_file} via Wikimedia Commons"
        elif (summary.get("originalimage") or {}).get("source"):
            image_url = summary["originalimage"]["source"]
            attribution = "Image via Wikimedia Commons"
        return WikiPageRecord(
            title=summary.get("title") or title,
            url=((summary.get("content_urls") or {}).get("desktop") or {}).get("page")
            or f"{base}/wiki/{quote(title.replace(' ', '_'))}",
            summary=summary.get("extract"), text=text[:MAX_TEXT_CHARS], image_url=image_url,
            image_attribution=attribution, data_origin="live", fetched_at=datetime.now(UTC),
        )

    async def geosearch(self, latitude: float, longitude: float, radius_m: float, limit: int = 30) -> list[dict]:
        """Geotagged articles around a point, with each article's short description and coordinate type.

        One request (``generator=geosearch`` + ``prop=coordinates|description``) so callers can tell a temple
        ("Hindu temple in Puri") from a constituency or a school without fetching every page.
        Returns ``[{"pageid", "title", "lat", "lon", "type", "description"}]``, nearest first; [] on failure.
        """
        base = f"https://{self._lang}.wikipedia.org"
        params = {
            "action": "query", "generator": "geosearch",
            "ggscoord": f"{latitude:.6f}|{longitude:.6f}",
            "ggsradius": str(int(min(radius_m, 10000))),
            "ggslimit": str(min(limit, 500)),
            "prop": "coordinates|description", "coprop": "type", "colimit": "max",
            "format": "json", "formatversion": "2",
        }
        try:
            data = await self._get(f"{base}/w/api.php", params=params)
        except (ExternalServiceError, ValueError) as exc:
            logger.warning("wikipedia_geosearch_failed", extra={"error": str(exc), "error_type": type(exc).__name__})
            return []
        pages = (data.get("query") or {}).get("pages") or []
        if isinstance(pages, dict):  # formatversion=1 shape
            pages = list(pages.values())
        out = []
        for page in pages:
            coords = (page.get("coordinates") or [None])[0]
            if not coords or page.get("missing") or "title" not in page:
                continue
            out.append({"pageid": page.get("pageid"), "title": page["title"], "lat": coords["lat"],
                        "lon": coords["lon"], "type": coords.get("type"), "description": page.get("description")})
        out.sort(key=lambda p: (p["lat"] - latitude) ** 2 + (p["lon"] - longitude) ** 2)
        return out


class MockWikipediaCollector:
    """Serves data/sample/wikipedia_pages.json (synthetic paraphrases, labelled mock)."""

    data_origin = "mock"

    def __init__(self, sample_dir: Path) -> None:
        self._data = json.loads((sample_dir / "wikipedia_pages.json").read_text(encoding="utf-8"))

    async def get_page(self, title: str | None = None, wikidata: str | None = None) -> WikiPageRecord | None:
        if not title and wikidata:
            title = self._data["wikidata_titles"].get(wikidata)
        page = self._data["pages"].get(title or "")
        if not page:
            return None
        return WikiPageRecord(
            title=page["title"], url=page["url"], summary=page["extract"], text=page["text"],
            license=page.get("license", "CC BY-SA 4.0"), image_url=None, data_origin="mock",
            fetched_at=datetime.now(UTC),
        )
