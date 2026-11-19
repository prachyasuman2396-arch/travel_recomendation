# Radius: production audit (Nov 2026)

What was reviewed: the FastAPI backend, the Streamlit "lite" mode, the LLM layer, the India rules, and the Docker / Render setup.
Each fix below has a code change in this branch. The new tests sit in `tests/unit/test_india_domain.py`, `tests/unit/test_llm_resilience.py` and `tests/integration/test_api.py`.

> **Verified (Python 3.13):** 351 tests pass, 5 PostGIS tests skipped (need `TEST_DATABASE_URL`). Ten tests had already been failing on the original commit, because that commit changed defaults without updating them (Groq `plain_text` mode, Groq-first ranking, the `ovp.dresel.systems` Overpass mirror). They are updated to the current behaviour. Offline eval: 31/31 scenarios, 0 hard violations. Ruff: 12 existing findings, none in changed lines.

## Top 5 issues

| # | Issue | Where | Risk | Status |
|---|---|---|---|---|
| 1 | **Fake reviews shown as real.** Live mode invented a rating, a review count and three reviews with made-up author names ("Priya N.", …) for every place, labelled them `community` / `data_origin="live"`, and the UI said "Live ratings, reviews". These numbers also fed ranking and LLM explanations ("Rated 4.7 from 6,212 ratings"). | `lite/live_data.py` `enrich_poi_reviews`, `streamlit_app.py` | **Critical** (misleads users; trust and legal exposure) | Fixed: off by default (`synthetic_reviews=False`). When switched on, the data is labelled `synthetic` / `mock`, with a note. The UI wording is corrected. |
| 2 | **Non-Hindu entry rule both leaked and over-filtered.** It used plain substring matching on the name *plus the Wikipedia description*, so: "Kalinga **school** of architecture" or "near the railway **station**" in a Jagannath article made it "secular" and let it through. A "temple" mention excluded Raghurajpur craft village. "math" matched "aftermath", "puri" matched "purity". A ±0.2° box (~22 km) around Puri excluded churches and everything nearby. Lingaraj (viewing platform only) and its compound shrines were *kept*. Guruvayur, Padmanabhaswamy and Ananta Vasudeva were caught only by exact phrases. | `services/india/rules.py` | **Critical** (core product promise) | Fixed: word-boundary matching on **name + category only**. Ordered checks: secular / other-faith / viewing-platform → orthodox names (India-wide, or Puri-only) → Puri 6 km zone → Lingaraj compound (200 m) → "Hindus only" wording. `non_hindu_entry: allowed=false` now always excludes. 15 parametrised tests added. |
| 3 | **No seamless LLM fallback, and a 429 stalled the request.** The deterministic ranker was opt-in (`RECOMMENDATION_DETERMINISTIC_FALLBACK=false`), so in the API a Gemini + Groq rate limit returned **503**. A 429 was retried on the *same* provider with backoff (up to 3×, 8 s max wait) before falling back, with a 60 s timeout per call. Retry-After was parsed but ignored. Truncated output (`MAX_TOKENS` / `length`) was thrown away entirely. | `llm/llm_service.py`, `core/config.py`, LLM clients | **High** | Fixed: deterministic fallback on by default (labelled in the response). A 429 skips straight to the next provider; a Retry-After over 2 s is never waited for. Gemini `RetryInfo.retryDelay` is now parsed. Truncated answers are salvaged (complete items kept). |
| 4 | **Brittle JSON parsing.** The fence regex only matched fences at line start/end. Prose around the JSON, an unterminated fence, trailing commas or a truncated array failed the *whole* response. One bad item rejected all items. Intent, fact-extraction and judge parsers used bare `json.loads`, so Groq plain-text output with fences always fell back to rules. | `recommendation/validator.py`, `intent/parser.py`, `agents/*.py` | **High** | Fixed: `llm/json_repair.py` (fences anywhere, prose, trailing commas, truncation repair that only removes text or closes brackets). Items are validated one by one. `reason` / `explanation` / `description` aliases are kept. Used by all four parsers. |
| 5 | **No connection reuse, no abuse protection, weak probes.** The Streamlit path ran `asyncio.run` per click with a *new* `httpx.AsyncClient` each time: DNS + TCP + TLS repeated for every host on every call. The API client had default limits and no HTTP/2. `POST /recommendations` (spends LLM tokens) had no rate limit. `/health` always returned 200, even with the DB down. The Docker image was single-stage. Overpass only started *after* Wikipedia GeoSearch finished. | `lite/runtime.py`, `streamlit_app.py`, `core/http.py`, `main.py`, `Dockerfile`, `lite/live_data.py` | **Medium–High** | Fixed: one background event loop + one pooled HTTP/2 client for the Streamlit process (`lite/loop.py`); `build_async_client` with `Limits(max_keepalive_connections=20)`. Per-IP limit on `/recommendations` and `/refine` (429 + `Retry-After`). `/healthz` (liveness) and `/readyz` (503 when the DB is down). Multi-stage, non-root (uid 10001) image with a HEALTHCHECK. Overpass now runs concurrently with GeoSearch. |

### Found while running the app (fixed)

- **Live mode recommended non-places.** Wikipedia GeoSearch returns every geotagged article, and each one was labelled "historical". For Puri that meant "Puri (city)", "Puri Assembly constituency", "Puri Zilla School" and "Bring Your Own Film Festival". In Bhubaneswar, 50 results within 10 km were mostly universities and constituencies, so real attractions never appeared. The fix is in `lite/wiki_places.py` plus `WikipediaClient.geosearch`. One batched request (`generator=geosearch&prop=coordinates|description`) now returns each article's short description. Pages are classified from it: a clear attraction word keeps a page, a strong non-place word (constituency, school, station…) drops it, then other attraction words keep it and weaker words (city, village, residence…) drop it. Up to 100 hits are fetched and the 40 nearest kept. Categories come from the description, so a pillar is tagged "historical", not a temple. The same Puri search now returns Aruna Stambha, Atharanala bridge and Puri Beach, with 14 restricted temples skipped.
- **Known gap:** "Village" pages are dropped, so heritage villages such as Raghurajpur only appear when OpenStreetMap is reachable.
- **Overpass is effectively down from some networks.** From the audit machine, overpass-api.de returned HTTP 406 for every User-Agent, `ovp.dresel.systems` hit its 7 s query timeout (0 places), and kumi / private.coffee returned 502 or timed out. The app now degrades correctly to Wikipedia-only. For production, self-host Overpass or use a paid mirror.

### Also found (not fixed in this pass)

- **Dual pricing gaps:** fixed a silent case where a fee cap was set but only the *other* tier's price was known; it now gives a warning notice and a small penalty instead of assuming the cheaper price. Also added `foreigner_entry` enforcement (the fact existed but was never used). Missing wheelchair data still only gives a notice and a 0.05 penalty. That is reasonable, but a "known accessible only" filter would be a product decision.
- **Unverified "no entry" facts now exclude** (they already did before, except when a viewing platform was listed). An LLM-extracted unverified fact can therefore hide a temple. That is the safe direction for this rule, but review the extraction precision.
- **Streamlit `cache_resource` shares a mutable `MemoryPoiStore` across all sessions.** It is read-only after load, and now that all async work runs on one loop thread, writes are serialised. Keep it that way: never mutate the store from a request.
- **The streamlit `cached_area` key ignores the API keys** (`_keys`), so the first visitor's embedding model decides the cache entry for everyone in that cell. This is harmless, but keep it in mind.
- **Broad `except Exception` remain at 24 sites (6 in the Streamlit UI).** Most are deliberate "degrade, don't crash" boundaries: each Wikipedia page, optional enrichment steps, probes, and Streamlit UI fallbacks. Narrowing them without running the suite risks turning a degraded answer into a 500. Next step: make each one `logger.exception(...)` or `exc_info=True` with a stable event name, then narrow them one by one with tests.
- **Per-step latency** is already reported (`timings_ms`: understand / retrieve / filter_and_score / llm / itinerary) and logged as `recommendation_completed` with the request ID. Area loading in lite mode is not broken down into steps yet.
- **Latency target (< 2.5 s):** achievable for *warm* recommendations (area cached, pooled connections, Groq first, 10 s LLM timeout, no same-provider 429 sleeps). A *cold* area load (GeoSearch + 6 Wikipedia articles + fact extraction + embeddings) will usually exceed it. Measure with the timings above before promising it.
- **CORS** is already strict (an explicit origin list, no credentials). **Admin auth** is a constant-time bearer token and is disabled when unset; that is good. **Error envelope** `{"error": {code, message, details, request_id}}` is consistent, now including 429.

## Production deployment checklist

### Environment variables

| Variable | Recommended | Notes |
|---|---|---|
| `GEMINI_API_KEY`, `GROQ_API_KEY` | set both | Each is the other's fallback; secrets only, never in the image |
| `LLM_PROVIDER` / `LLM_FALLBACK_PROVIDER` | `groq` / `gemini` for latency | Groq answers faster; Gemini is the quality fallback |
| `LLM_TIMEOUT` | `10`–`15` | Default 60 s is far beyond the latency budget |
| `LLM_MAX_RETRIES` | `2` | A 429 no longer consumes retries when a fallback exists |
| `RECOMMENDATION_DETERMINISTIC_FALLBACK` | `true` (new default) | Response is labelled `generation_method="deterministic_fallback"` |
| `RATE_LIMIT_PER_MINUTE` | `20` per IP | `0` disables |
| `TRUST_FORWARDED_FOR` + uvicorn `FORWARDED_ALLOW_IPS` | `true` + proxy CIDR | Only behind your own proxy; never `*` |
| `HTTP_MAX_CONNECTIONS` / `HTTP_MAX_KEEPALIVE_CONNECTIONS` | `100` / `20` | Per process; HTTP/2 via `httpx[http2]` |
| `WEATHER_CACHE_TTL_SECONDS` | `600` | Open-Meteo updates hourly; ~1 km cache grid |
| `CORS_ORIGINS` | exact frontend origin(s) | No wildcards |
| `ADMIN_API_TOKEN` | 32+ random bytes, or unset | Unset disables admin endpoints |
| `CONTACT_EMAIL` | an ops mailbox | Required by Wikimedia / OSM etiquette (User-Agent) |
| `LOG_JSON` / `LOG_LEVEL` | `true` / `INFO` | One JSON line per event, with `request_id` |
| `WEB_CONCURRENCY` | `2` (1 vCPU) | Each worker has its own pool, cache and rate-limit budget |

### Caching: in-memory TTLCache vs Redis

- **One instance, one worker (Render free, Streamlit Cloud):** keep the in-process `TTLCache` (weather 600 s), the Streamlit `cache_resource` (areas 6 h) and the in-process rate limiter. Nothing to run.
- **Several workers or instances:** move three things to Redis. (1) Rate-limit counters (`INCR` + `EXPIRE` on `rl:{path}:{ip}:{minute}`); otherwise the effective limit is N × the configured one. (2) Weather / AQI snapshots (`SETEX` 600 s, same key grid). (3) The LLM result cache, if one is added. Area knowledge bases are large: rebuild them per instance rather than serialising them.
- Never cache responses that depend on the visitor profile without the full profile in the key: non-Hindu / foreign / fee cap change the result.

### Connection pools and timeouts

- Outbound: one `httpx.AsyncClient` per process (API) or per background loop (Streamlit). Settings: `max_connections=100`, `max_keepalive_connections=20`, `keepalive_expiry=30s`, HTTP/2 on; connect timeout 5–10 s, read timeout 10 s (lite) or 30 s (API).
- Overpass: 1 s grace after GeoSearch, then skipped. Full wait only when GeoSearch is empty.
- Database: `DB_POOL_SIZE=5`, `DB_MAX_OVERFLOW=10` per worker. Keep `workers × (pool + overflow)` below Postgres `max_connections` minus 10.

### Rate limits (upstream)

- **Wikimedia:** keep `wiki_concurrency ≤ 6` and an identifying User-Agent. A 429 is degraded per page (a note, not a failure).
- **Overpass (public):** ~2 concurrent queries per IP. Use the mirror list and fail over; consider a self-hosted instance for production volume.
- **Open-Meteo free tier:** 10k calls/day, non-commercial. Use `WEATHER_API_KEY` (commercial host) in production.
- **Groq / Gemini:** set the per-key RPM/TPM budget so that `RATE_LIMIT_PER_MINUTE × expected IPs` stays under it; the deterministic fallback absorbs the rest.

### Health and deploy

- Orchestrator **liveness → `/healthz`**, **readiness → `/readyz`**; `/health` stays as a human-readable status page. Render (Streamlit) keeps `/_stcore/health`.
- Image: `docker build backend` gives a multi-stage, non-root image with a HEALTHCHECK on `/healthz`. Run `alembic upgrade head` as a separate release step, not on every container start, once there are several replicas.
- Before go-live: run the offline eval (`python scripts/run_eval.py`). Hard violations must be 0, including the updated Lingaraj scenarios.
