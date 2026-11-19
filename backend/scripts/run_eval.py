"""Run the offline India evaluation (no database, no keys needed).

  python scripts/run_eval.py                    # deterministic mock LLM, reproducible
  python scripts/run_eval.py --llm configured   # use the Gemini/Groq chain from .env (costs tokens)

Writes data/reports/eval_<timestamp>/{report.md,results.json} and exits non-zero if any
hard violation occurred (so it can gate CI).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.eval.harness import load_scenarios, run_scenarios, summarize, write_report
from app.eval.offline import build_offline_store
from app.services.data_collection.factory import build_http_client
from app.services.ingestion.memory_store import MemoryCandidateSource
from app.services.intent.parser import RuleIntentParser
from app.services.llm.llm_factory import build_llm_service
from app.services.llm.llm_service import LLMService
from app.services.llm.mock_client import MockLLMProvider
from app.services.recommendation.engine import EngineConfig, RecommendationEngine
from app.services.sentiment.lexicon import LexiconSentimentAnalyzer
from app.services.sentiment.service import SentimentService
from app.services.weather.open_meteo import MockWeatherProvider
from app.services.weather.service import WeatherService


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--llm", choices=["mock", "configured"], default="mock")
    parser.add_argument("--only", help="comma-separated scenario ids")
    args = parser.parse_args()

    settings = get_settings()
    data_dir = settings.data_dir
    store = await build_offline_store(data_dir)
    source = MemoryCandidateSource(store, SentimentService(LexiconSentimentAnalyzer(), settings.sentiment_min_reviews))
    weather = WeatherService(MockWeatherProvider(data_dir / "sample" / "weather_open_meteo.json"))
    async with build_http_client(settings) as http:
        llm = build_llm_service(settings, http) if args.llm == "configured" else LLMService([MockLLMProvider(6)])
        engine = RecommendationEngine(source, weather, llm, EngineConfig(max_results=6),
                                      intent=RuleIntentParser())
        scenarios = load_scenarios()
        if args.only:
            wanted = set(args.only.split(","))
            scenarios = [s for s in scenarios if s["id"] in wanted]
        results = await run_scenarios(engine, source, scenarios)

    summary = summarize(results)
    out = data_dir / "reports" / f"eval_{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    report = write_report(results, summary, out)
    print(json.dumps(summary, indent=2))
    for r in results:
        if not r.passed:
            print(f"FAIL {r.id}: {r.violations + r.failed_expectations + ([r.error] if r.error else [])}")
    print(f"report: {report}")
    return 1 if summary["hard_violations"] else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
