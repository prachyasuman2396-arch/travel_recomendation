"""Run data ingestion + offline curation from the command line.

Examples:
  python scripts/ingest.py --mode mock                 # India demo, no keys: places,osm,wikipedia + curated + agents
  python scripts/ingest.py --mode live --radius-km 20  # live Google Places + OSM + Wikipedia
  python scripts/ingest.py --source yelp               # optional extra sources run only when asked
  python scripts/ingest.py --skip-agents               # ingestion only

Default sources come from DEFAULT_INGEST_SOURCES (places,osm,wikipedia). Order matters:
Google Places creates POIs, OSM links/adds (with Odia/Hindi names), Wikipedia attaches background,
then curated verified facts + the festival calendar are loaded, then the rule-based fact
extraction agent proposes *unverified* facts with verbatim evidence.

mode=auto uses live APIs where keys are configured and mock otherwise (keyless OSM/Wikipedia
follow the Google key so a keyless demo stays all-mock); ingestion_runs records what ran.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.v1.admin import build_pipeline, load_curated_files
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.database.session import session_scope
from app.repositories.agent_repository import AgentRepository
from app.repositories.sentiment_repository import SentimentRepository
from app.services.agents.fact_extraction import FactExtractionAgent, RuleFactExtractor
from app.services.data_collection.factory import ALL_SOURCES, build_collector, build_http_client, default_sources
from app.services.sentiment.lexicon import LexiconSentimentAnalyzer
from app.services.sentiment.service import SentimentService
from app.services.sentiment.transformer import TransformerSentimentAnalyzer


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=[*ALL_SOURCES, "default", "all"], default="default",
                        help="default = DEFAULT_INGEST_SOURCES; all = every source incl. yelp/flickr")
    parser.add_argument("--mode", choices=["auto", "live", "mock"], default="auto")
    parser.add_argument("--lat", type=float, default=20.2961)
    parser.add_argument("--lon", type=float, default=85.8245)
    parser.add_argument("--radius-km", type=float, default=None,
                        help="default: 70 km for mock (covers Puri/Konark samples), 25 km live")
    parser.add_argument("--skip-curated", action="store_true")
    parser.add_argument("--skip-agents", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level, json_logs=False)
    if args.source == "default":
        sources = default_sources(settings)
    elif args.source == "all":
        sources = list(ALL_SOURCES)
    else:
        sources = [args.source]
    sources = [s for s in ALL_SOURCES if s in sources]  # canonical order
    radius_km = args.radius_km or (70 if args.mode == "mock" else 25)

    summary: dict = {}
    async with build_http_client(settings) as http:
        for source in sources:
            collector = build_collector(source, settings, http, args.mode)
            with session_scope() as db:
                pipeline = build_pipeline(db, settings)
                if source == "places":
                    run_id, stats = await pipeline.ingest_places(collector, args.lat, args.lon, radius_km * 1000)
                elif source == "osm":
                    run_id, stats = await pipeline.ingest_osm(collector, args.lat, args.lon, radius_km * 1000)
                elif source == "wikipedia":
                    run_id, stats = await pipeline.ingest_wikipedia(collector)
                elif source == "yelp":
                    run_id, stats = await pipeline.ingest_yelp(collector, args.lat, args.lon, int(radius_km * 1000))
                else:
                    run_id, stats = await pipeline.ingest_flickr(collector, args.lat, args.lon, radius_km)
            summary[source] = {"run_id": run_id, "mode": collector.data_origin, "fetched": stats.fetched,
                               "inserted": stats.inserted, "updated": stats.updated, "rejected": stats.rejected,
                               "flagged": stats.flagged, "rejection_reasons": stats.rejection_reasons,
                               "notes": stats.notes}

    if not args.skip_curated:
        facts, events = load_curated_files(settings)
        with session_scope() as db:
            run_id, stats = await build_pipeline(db, settings).load_curated(facts, events)
        summary["curated"] = {"run_id": run_id, "fetched": stats.fetched, "inserted": stats.inserted,
                              "updated": stats.updated, "rejected": stats.rejected,
                              "rejection_reasons": stats.rejection_reasons}

    if not args.skip_agents:
        with session_scope() as db:
            report = await FactExtractionAgent(AgentRepository(db), [RuleFactExtractor()]).run(limit=500)
        summary["fact_extraction_agent"] = report.as_dict()

    analyzer = (TransformerSentimentAnalyzer(settings.sentiment_model)
                if settings.sentiment_backend == "transformers" else LexiconSentimentAnalyzer())
    with session_scope() as db:
        summary["sentiment"] = SentimentRepository(db).run(SentimentService(analyzer, settings.sentiment_min_reviews))
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
