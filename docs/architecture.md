# Architecture

## The one-paragraph version

The database finds what is **possible** (places within your radius, open today); deterministic code computes
what is **measurable** (distance, ratings, sentiment, weather fit, a coarse time fit); the LLM decides what is
**suitable** for this traveller and explains why; and a validator makes sure the LLM only talks about places
and facts it was given. Every LLM call happens *after* geographic filtering. The LLM never computes distances.

```
Browser (React)                     FastAPI                                PostgreSQL + PostGIS
───────────────                     ───────                                ────────────────────
Explore form ── POST /recommendations ─▶ RecommendationEngine
                                        │  A validate PreferenceProfile (Pydantic)
                                        │  B ST_DWithin + ST_Distance  ──────────────▶ pois (GiST index)
                                        │  D weather (Open-Meteo, cached) ┐ concurrent   + ratings/photos/reviews
                                        │    hard constraints: radius, closed today      (grouped sub-queries)
                                        │  C features + pre-score → top N (40)
                                        │  E LLMService: Gemini → retries → Groq
                                        │  F RecommendationValidator (ids, dupes, distance, grounding)
Results / Map ◀── facts from DB + explanations from LLM
```

## Layers

| Layer | Folder | Depends on |
|---|---|---|
| API routes (thin) | `app/api/v1` | services, repositories |
| Composition root | `app/api/deps.py` | settings → builds services |
| Domain services (pure, testable) | `app/services/*` | Pydantic, stdlib, pandas/sklearn |
| Repositories (SQL) | `app/repositories` | SQLAlchemy, GeoAlchemy2 |
| Models / migrations | `app/models`, `migrations/` | SQLAlchemy, Alembic |

Domain services never import SQLAlchemy or read settings. They receive plain values or small `Protocol`s
(`PoiStore`, `CandidateSource`, `LLMProvider`, `WeatherProvider`, `SentimentAnalyzer`). That is why most of
the logic is unit-tested without a database or network.

## Data pipeline

```
External API → client parser (validation) → raw store (if terms allow) → cleaning
             → entity resolution → internal POI id → idempotent upserts → ingestion_runs
```

* **Three independent clients** (`places_client.py`, `flickr_client.py`, `yelp_client.py`) convert provider JSON into
  normalised records. None imports another. Mock collectors feed provider-shaped sample JSON through the *same*
  parsers and stamp `data_origin="mock"`.
* **Places (New) Nearby Search has no pagination** (max 20 results/call, radius ≤ 50 km), so large areas are covered
  by tiling the circle into smaller circles (capped by `max_tiles` to protect quota). Text Search pagination via
  `pageToken` is implemented too.
* **Retries**: one policy (`core/retry.py`, Tenacity) with exponential backoff + jitter, used only for errors that
  declare `retryable` (429, 5xx, timeouts). Auth failures and Yelp's daily-quota 429 are never retried.
* **Raw data**: three layers are kept distinct — raw responses (`data/raw/<provider>/`, only for live data and only
  where enabled: Flickr on; Google/Yelp off by default because of their storage terms), normalised source rows
  (`poi_sources`, `yelp_businesses`, `flickr_photos`, `reviews`, `poi_ratings`) and unified `pois`.
* **Idempotency**: unique keys on `(provider, external_id)`, `external_photo_id`, `(source, external_review_id)` etc.
  plus `INSERT … ON CONFLICT DO UPDATE`. Reviews are only rewritten when their text hash or rating changed, so the
  sentiment cache stays valid.
* **Retention**: live Yelp content gets `expires_at = fetched_at + YELP_CONTENT_RETENTION_HOURS` and is removed by
  `scripts/purge_expired.py` (or `POST /admin/retention/purge`).

## Entity resolution (`services/entity_resolution/matcher.py`)

Weighted score over comparable components only:

| Component | Weight | Notes |
|---|---|---|
| Name similarity | 0.45 | RapidFuzz: mean of token-set and token-sort ratio on normalised names |
| Distance | 0.25 | 1.0 within 75 m (large attractions), linear to 0 at 400 m; beyond 400 m = not a candidate |
| Category compatibility | 0.20 | same group (outdoors / heritage / commerce / food / leisure) = 1, conflict = 0 **and −0.20 penalty** |
| Address similarity | 0.10 | skipped when either address has < 3 tokens (a bare "Bhubaneswar, Odisha" matches everything) |

Outcomes: **≥ 0.80 HIGH** → auto-link · **≥ 0.60 MEDIUM** → keep separate + `match_candidates` row for review ·
otherwise **LOW** → separate. Guards: name similarity < 0.55 is always LOW (proximity alone never merges), and two
near-equal strong candidates downgrade HIGH to MEDIUM. Thresholds are configurable (`MATCH_*`).

Tested hard cases (with the mock dataset): "Ekamra Kanan Cafe" 30 m from "Ekamra Kanan Botanical Gardens" stays
separate; "Nandankanan Zoo" ↔ "Nandankanan Zoological Park" (139 m) and "Lingaraja" ↔ "Lingaraj" auto-link;
"Dhauli Peace Pagoda" ↔ "Dhauli Shanti Stupa" is flagged.

**Flickr photos** attach at POI level, to at most one POI, and only with *textual* evidence (title/tags mention the
POI name, including concatenated tags like `ekamrakanan`). Proximity alone caps confidence at 0.4 < 0.55 threshold.
Photo counts are exposed only as "photo activity", never as visitor numbers.

## Geographic retrieval (`repositories/poi_repository.py`)

```sql
SELECT p.*, ST_Distance(p.location, :pt) AS distance_m, ratings.ratings, photos.photo_count, ...
FROM pois p
LEFT JOIN (SELECT poi_id, json_agg(...) FROM poi_ratings GROUP BY poi_id) ratings USING (poi_id)
LEFT JOIN (SELECT poi_id, count(*) ... FROM flickr_photos GROUP BY poi_id) photos USING (poi_id)
...
WHERE ST_DWithin(p.location, :pt, :radius_m)      -- index-backed (GiST on geography)
ORDER BY distance_m LIMIT :prefetch                -- 300
```

`:pt` is `ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography` — longitude first, values bound as parameters.
Review snippets for all candidates come from one windowed query (`row_number() OVER (PARTITION BY poi_id)`), so
there are no N+1 queries. The engine re-checks the radius in Python as defence in depth.

## Features (`services/feature_engineering/features.py`)

Deterministic and small on purpose: review-count-weighted rating, a Bayesian-adjusted rating (prior 3.8, weight 50),
log-scaled popularity and photo activity (relative within the candidate set), interest match (primary category 1.0,
secondary 0.7, photography via photo activity), sentiment balance, weather fit (rule table by outdoor/indoor
category, rain probability, storms, heat), and `time_fit` — a coarse straight-line round trip at
`ASSUMED_AVG_SPEED_KMH`, labelled as such and used only as a soft hint. `build_feature_frame` produces a
one-hot + min-max scaled matrix (scikit-learn) for analysis.

The **pre-score** (interest .35, rating .20, distance .15, sentiment .10, popularity .10, weather .10) only decides
which ~40 candidates the LLM sees. Preferences are soft signals there; the LLM may still pick a non-matching place
if the free text strongly suggests it.

## LLM layer (`services/llm/`)

* `GeminiProvider` and `GroqProvider` call the REST APIs with HTTPX (no SDKs → one HTTP stack, uniform error mapping,
  MockTransport tests). Gemini uses `responseMimeType=application/json` + `responseJsonSchema`; Groq uses
  `response_format.json_schema` with `strict: true` (or `json_object` mode for models without strict support).
* One hand-written JSON Schema (`response_schemas.py`) valid for both: no `$ref`, every property required,
  `additionalProperties: false`. The same Pydantic models validate every response.
* Error taxonomy (`base.py`): `retryable` = rate limit, timeout, server, invalid response; not retryable = auth,
  model unavailable, context overflow, blocked, truncated. All allow fallback.
* `LLMService`: provider chain from config (`LLM_PROVIDER`, `LLM_FALLBACK_PROVIDER`). Each provider gets
  `LLM_MAX_RETRIES` attempts for retryable errors, then the next provider. All fail → `AllProvidersFailedError` →
  HTTP 503 `recommendation_unavailable`. Context overflow → the engine halves the candidate list (up to twice).
* `MockLLMProvider` (`LLM_PROVIDER=mock`) produces deterministic templated output from the same input so the
  whole app works offline. Responses say `generation_method: "mock_llm"` and the UI labels it.
* `RECOMMENDATION_DETERMINISTIC_FALLBACK=true` (off by default) returns a labelled score-based ranking instead of
  a 503 when all providers fail.

## Validation of LLM output (`services/recommendation/validator.py`)

1. JSON (code fences tolerated) → Pydantic schema
2. `poi_id` must be in the supplied candidate set; duplicates dropped; distance re-checked; ranks renumbered; capped
3. Grounding heuristics, sentence by sentence: numeric distance/rating/review-count/temperature claims must match
   supplied facts; opening-hours claims are removed when no hours were supplied; any travel-time claim is removed
4. No weather supplied → `weather_suitability = "unknown"`
5. Nothing valid left → `LLMInvalidResponseError` (retry / fall back)

**Limits:** grounding is heuristic. It catches fabricated numbers and the claim types above, not every possible
qualitative overstatement ("the most beautiful garden in India"). Facts shown in the UI (distance, rating, hours,
sentiment) always come from the database, never from LLM text.

## Sentiment (`services/sentiment/`)

Default backend is a deterministic lexicon with negation/intensifier handling scoped to clauses (Yelp gives ≤ 3
excerpts per business, so a large model adds little by default). `SENTIMENT_BACKEND=transformers` uses
`nlptown/bert-base-multilingual-uncased-sentiment` (BERT, 1–5 stars) through the same interface. Aspects
(environment, cleanliness, facilities, food, safety, accessibility, visitor experience, crowding) are only reported
when a clause mentions them. Results are cached per `(review_id, model)` with the text hash; POIs with fewer than
`SENTIMENT_MIN_REVIEWS` analysed reviews get `insufficient_data`.

## Weather (`services/weather/`)

Open-Meteo (no key needed; `WEATHER_API_KEY` switches to the commercial host). Current observation and hourly
forecast are distinct (`kind: current | forecast`), carry `valid_at`, `retrieved_at` and `source`, and are cached
~10 min per ~1 km grid cell. Any failure → recommendations continue without weather and the UI says so.

## India design (Phase 1–5)

### Principle: agents offline, deterministic request path

```
OFFLINE (admin / scheduled)                                   REQUEST PATH (visitor waits, ~10–60 ms + LLM)
──────────────────────────                                   ──────────────────────────────────────────────
Google Places ─┐                                             intent (rules | LLM closed schema, rules merged)
OSM Overpass ──┼─▶ ingestion pipeline ─▶ pois, alt_names     PostGIS candidates + facts + docs + embeddings
Wikipedia ─────┘        │                  poi_documents      weather + AQI + events (concurrent)
curated JSON ───────────┴──────────────▶ poi_facts (verified) rules (verified → exclude; else notice/penalty)
                                          events              season + AQI + semantic → pre-score
fact_extraction agent ─▶ poi_facts (unverified, quoted)       LLM rank + cite F-ids → validator
match_judge agent ─────▶ links / merges (≥0.85 only)          notices attached by code → itinerary
embeddings agent ──────▶ poi_embeddings                       response (+ timings_ms)
freshness agent ───────▶ business_status, retention purge
```

No agent runs on the request path. That keeps latency predictable, and it means a model failure there only
affects the explanation, never which rules apply.

### Fact governance (`services/india/facts.py`, `rules.py`)

| status | from | effect |
|---|---|---|
| `verified` | `data/curated/*.json` (each with `source_url`) or admin approval | may **exclude** a place (`entry_restricted`, `over_budget`, `not_wheelchair_accessible`) and is shown with its source |
| `unverified` | fact-extraction agent (evidence quote must appear verbatim in the source text), Google/OSM attributes | shown as "Reported: …", soft penalty only |
| `rejected` | admin | ignored |

Fact values are typed (`EntryFeeValue`, `NonHinduEntryValue` …) and validated on every write. A verified fact
is never downgraded by a later agent run: the SQL upsert uses a `CASE` on status. Religion is never assumed:
without the non-Hindu toggle, entry rules appear as *info* notices and never filter anything.

### Indian names and matching

Indian names need extra handling before matching:

* `normalize_name` maps local words (`mandir → temple`, `sagar → lake`, `kanan → garden`, `gumpha → caves`) and
  drops honorifics (`shri`, `maa`).
* Punctuation is removed by Unicode category. Odia and Devanagari vowel signs are not `\w`, so a regex would
  shred them.
* `compact_similarity` handles spacing variants (`Bindusagar` vs `Bindu Sagar Lake`).
* `distinctive_conflict` stops "Odisha *Crafts* Museum" from matching "Odisha *State* Museum".

OSM records are matched against all known names, including the Odia and Hindi `alt_names`.

### Seasons, air quality, festivals

* `indian_season` uses IMD-style seasons: summer Mar–May, monsoon Jun–Sep, post-monsoon Oct–Nov, winter Dec–Feb.
  Each season gives small category adjustments, for example waterfalls up in the monsoon and beaches down.
* US AQI from Open-Meteo downgrades the weather fit of outdoor places.
* Festivals come from `events`. The engine includes events up to two days ahead of the visit as notices, because
  crowds build early. The avoid-crowds **penalty**, however, only applies to events that overlap the visit window.

### Semantic matching

Candidates are geo-filtered first, so at most a few hundred vectors are compared per request. NumPy cosine over
`float[]` columns is enough, and pgvector isn't needed. Embeddings come from Gemini (`gemini-embedding-001`, 768
dimensions, `RETRIEVAL_DOCUMENT` / `RETRIEVAL_QUERY`) and are built offline. The fallback is TF-IDF with an
Indian-travel synonym expansion (`sagar → lake`, `gufa → caves`). The score blends 0.85 rule/feature score with
0.15 semantic score.

### Citations

Each candidate's facts get per-request IDs `F1…Fn` in the prompt. The model returns `cited_fact_ids`. The
validator then:

* drops IDs that don't exist for that place
* removes any explanation sentence quoting a rupee amount that doesn't match a sourced fee

Notices are attached after validation, so the LLM can neither remove nor reword a rule.

### Itinerary (`services/itinerary/planner.py`)

1. A travel matrix comes from OSRM (`/table`), or from a labelled estimate: straight line × 1.35 at 22 km/h.
2. Opening hours are parsed into windows ("6:00 AM – 12:30 PM, 3:30 – 9:00 PM"), and a midday closure is
   subtracted when a fact says so.
3. An exhaustive search runs over orderings of the top ≤ 6 places. The plan with the most stops wins; ties go to
   rank, then to less travel.
4. The planner waits for openings when it can, and skips (and reports) places that don't fit.

### Evaluation (`app/eval/`)

`build_offline_store` runs the real pipeline into memory. The harness runs `scenarios.json` (31 India
scenarios) through the real engine and checks hard rules against stored data:

* radius
* closed today
* avoided category
* verified fee cap
* wheelchair access
* non-Hindu entry, plus a notice when entry is allowed only from a viewing platform
* unknown citations
* itinerary length

`tests/unit/test_agents_semantic_sources.py` runs the harness in CI and also checks that it detects a planted
violation.

## Things intentionally not built

No user-interaction tracking or `user_interactions` table; no Redis/Kafka/vector DB (geo-filter first, then NumPy cosine); no LangChain or agent framework (agents are plain classes with Protocols). Admin auth is a
single bearer token (fine for one operator; replace with real auth before multi-user admin).
