# Radius — an AI tourist recommender built for India

Tell it where you are, what you enjoy, who is travelling and how long you have — in English or Hinglish
("purane mandir dikhao, bheed kam, sirf 4 ghante"). It finds nearby places with PostGIS and checks them
against the things that actually decide a day out in India:

* **Temple entry rules**: for example, non-Hindus may not enter Lingaraj or Jagannath Puri.
* **Foreigner vs Indian ticket prices**.
* **Opening hours**, including midday temple closures and Monday museum closures.
* **Festival crowds** from a sourced calendar (Kartika Purnima, Rath Yatra, Konark Dance Festival).
* **The Indian season**: summer, monsoon, post-monsoon or winter.
* **Air quality**.

It then asks **Gemini** (with **Groq** as fallback) to rank and explain the best fits. The model must cite the
sourced facts it relied on. Finally, the app builds a timed **itinerary** that fits your hours. You can refine the
result conversationally ("fewer temples", "kuch khana bhi").

Two design rules drive the whole system:

1. **Agents curate knowledge offline; the request path stays fast and deterministic.** LLM agents extract facts
   from Wikipedia text, judge uncertain duplicate places and index embeddings. They never run while a visitor waits.
2. **Only verified facts can hide a place.** A fact is verified when it comes from a curated source, or when an
   admin approves it after reading the source quote. Anything an agent or a map extracted is shown as
   *"Reported: …"*. It can lower a place's ranking but never hides it. Rule notices are attached by code after the
   LLM runs, so the model cannot drop them.

The app runs fully **offline on clearly-labelled mock data** before you have any API keys. The curated facts and
festival dates in `data/curated/` are **real and sourced**: they were checked on 17 Nov 2026 and each carries
`source_url`.

* Architecture and India design: [`docs/architecture.md`](docs/architecture.md)
* Routing (OSRM vs estimates): [`docs/routing.md`](docs/routing.md)

## Fastest way to run it: the free Streamlit edition

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py      # paste a free Gemini and/or Groq key in the sidebar
```

* **No database and no paid APIs.** Places come from OpenStreetMap, background from Wikipedia, and weather
  and air quality from Open-Meteo.
* **Gemini and Groq are routed per task**, each the other's fallback, and the app discovers which models
  your key can use.
* **Deploys for free** on Streamlit Community Cloud or Render. See **[DEPLOY.md](DEPLOY.md)**.

The full FastAPI + React + PostGIS stack below is for production scale with Google Places.

---

## Stack

| | |
|---|---|
| Frontend | React 19, TypeScript, Vite, Tailwind CSS v4, shadcn/ui-style components, React Router 7, TanStack Query, React Hook Form + Zod, Leaflet + OpenStreetMap |
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, GeoAlchemy2, Alembic, HTTPX, Tenacity |
| Data / ML | pandas, NumPy, scikit-learn (TF-IDF fallback), RapidFuzz, lexicon sentiment (optional BERT), Gemini `gemini-embedding-001` |
| Database | PostgreSQL 16 + PostGIS 3.4 |
| LLMs | Google Gemini (primary), Groq (fallback) — no OpenAI |
| Data sources | **Google Places API (New)** (places, hours, reviews, photos, accessibility) · **OpenStreetMap Overpass** (Odia/Hindi names, wheelchair tags) · **Wikipedia/Wikidata** (background, images) · **Open-Meteo** (weather + air quality) · curated sourced facts and festival calendar · *optional* Yelp, Flickr |
| Ops | Docker Compose (optional OSRM profile), pytest, Ruff, ESLint, Vitest |

## How a request flows

```
"I'm not Hindu, from abroad with my elderly mother, history, avoid crowds, only 4 hours"
  │
  ├─ understand   rule parser (English/Hinglish) or LLM closed-schema parser → visitor flags, categories,
  │               hours. Free text can only ADD restrictions.
  ├─ retrieve     PostGIS ST_DWithin + facts, Wikipedia summary, local names, embeddings — and, concurrently,
  │               weather + AQI and festivals in the visit window
  ├─ rules        verified facts → hard exclusions (entry_restricted, over_budget, not_wheelchair_accessible,
  │               avoided_category); everything else → notices + soft penalties; season and AQI adjustments
  ├─ semantic     Gemini embeddings (cosine) or TF-IDF with an Indian-travel synonym list (sagar → lake …)
  ├─ rank         Gemini → Groq; prompt carries fact IDs F1…; validator drops unknown IDs and checks ₹ claims
  │               against sourced fees
  ├─ itinerary    OSRM table (or labelled estimate) + opening-hours windows → best feasible ordered plan
  └─ response     ranked items + notices + cited facts + season + AQI + events + itinerary + timings
```

## Project layout

```
backend/
  app/
    api/v1/            pois, recommendations (+ /refine), events, users, weather, admin (+ agents, facts)
    api/deps.py        composition root: builds services from settings
    services/
      data_collection/ places_client, osm_client, wikipedia_client, (yelp, flickr), mock collectors
      preprocessing/   cleaning (Indic-safe names, synonyms, PIN codes), categories (+ name hints)
      entity_resolution/ matcher with compact/distinctive-token checks for Indian spellings
      ingestion/       pipeline (places → osm → wikipedia → curated → yelp), memory store for eval
      india/           facts (typed values, governance), rules (visitor rules), seasons
      agents/          fact_extraction (grounded), match_judge, maintenance (embeddings, freshness)
      intent/          rule + LLM intent parsers
      semantic/        embeddings / TF-IDF scoring
      routing/         OSRM + estimate
      itinerary/       opening-hours windows + permutation planner
      recommendation/  engine, payload (fact IDs), validator (citations, ₹ checks), fallback
      weather/         Open-Meteo weather + air quality
      llm/             gemini, groq, mock, service with fallback
    eval/              offline knowledge base, 31 India scenarios, harness
  migrations/          0001_initial, 0002_india (facts, documents, embeddings, events, alt names)
  scripts/             ingest.py, run_eval.py, run_eda.py, purge_expired.py, build_mock_dataset.py
data/
  curated/             REAL sourced facts (poi_facts.json) + festival calendar (events_odisha.json)
  sample/              MOCK provider responses (places, osm, wikipedia, weather, air quality, yelp, flickr)
docs/                  architecture.md, routing.md
```

---

## Quick start (Docker, no API keys)

```bash
cp .env.example .env
# For a first run without keys:  LLM_PROVIDER=mock  WEATHER_PROVIDER=mock  ADMIN_API_TOKEN=<long random string>
docker compose up --build -d
docker compose exec backend python scripts/ingest.py --mode mock   # places, osm, wikipedia + curated + fact agent
```

* App: http://localhost:8080
* API docs: http://localhost:8000/docs

Everything mock is labelled "sample data" and "Offline demo ranking". Add keys to `.env` and set
`LLM_PROVIDER=gemini` for the real model.

## Local development

```bash
docker compose up -d db
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export DATABASE_URL=postgresql+psycopg://tourist:change-me@localhost:5432/tourist
alembic upgrade head
python scripts/ingest.py --mode mock
uvicorn app.main:app --reload --port 8000

cd ../frontend && npm install && npm run dev      # http://localhost:5173
```

## Configuration

All settings are environment variables (`backend/app/core/config.py`, documented in `.env.example`). The India
related ones:

| Setting | Default | Meaning |
|---|---|---|
| `DEFAULT_INGEST_SOURCES` | `places,osm,wikipedia` | what `scripts/ingest.py` runs; Yelp/Flickr only when asked |
| `PLACES_INCLUDE_REVIEWS` | `true` | Google reviews + `goodForChildren` (Enterprise + Atmosphere SKU — costs more) |
| `GOOGLE_CONTENT_RETENTION_DAYS` | `30` | Google reviews older than this are purged by the freshness agent |
| `OVERPASS_URL`, `CONTACT_EMAIL` | public Overpass | set a contact e-mail; Overpass/Wikimedia ask for an identifying User-Agent |
| `INTENT_PARSER` | `rules` | `llm` uses Gemini/Groq with a closed schema (rules still merged in) |
| `SEMANTIC_BACKEND` | `gemini` | uses embeddings once the embeddings agent has indexed places; otherwise TF-IDF |
| `OSRM_URL` | empty | road travel times; empty → straight line × 1.35 at 22 km/h, labelled "estimated" |
| `AIR_QUALITY_ENABLED` | `true` | Open-Meteo AQI; poor air downgrades outdoor places |

Missing keys never crash the app: `mode=auto` ingestion falls back to mock (recorded as such), keyless OSM and
Wikipedia follow the Google key so a keyless demo stays consistently mock, and a missing LLM gives a clear 503
unless `LLM_PROVIDER=mock`.

## Data and curation

```bash
cd backend
python scripts/ingest.py --mode live --radius-km 25    # Google → OSM → Wikipedia → curated → fact agent → sentiment
python scripts/ingest.py --source yelp                 # optional extra sources
```

In the app, **Data management** (`/admin`) can do the following:

* run each source
* load curated data
* run the four agents:
  * fact extraction
  * match judge
  * embeddings
  * freshness
* **review unverified facts**: you see the verbatim evidence quote and a link to the source before you verify or
  reject

To add a verified fact, put it in `data/curated/poi_facts.json` with a `source_url` and press *Load curated facts*.

## API (new or changed for India)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/recommendations` | body `PreferenceProfile` (+ `visitor`); returns notices, cited facts, season, AQI, events, itinerary, timings |
| POST | `/api/v1/recommendations/refine` | `{profile, message}` → updated profile, what changed, new results |
| GET | `/api/v1/pois/{id}` | + `facts` (with status/source/evidence), `background` (Wikipedia), `alt_names` |
| GET | `/api/v1/pois/{id}/photo` | redirect to a short-lived Google photo URL (key stays server-side); 404 for mock |
| GET | `/api/v1/events` | festival calendar (`start`, `days`) |
| POST | `/api/v1/admin/ingestion/{places\|osm\|wikipedia\|yelp\|flickr}` | run a collector |
| POST | `/api/v1/admin/curated/load` | load `data/curated/*.json` |
| POST | `/api/v1/admin/agents/{fact_extraction\|match_judge\|embeddings\|freshness}` | run an offline agent |
| GET / POST | `/api/v1/admin/facts` · `/api/v1/admin/facts/{id}` | list facts by status; verify / reject |

Existing endpoints (`/pois`, `/pois/nearby`, reviews, photos, users, weather, dashboard, match candidates,
retention) are unchanged. Errors use `{"error": {"code", "message", "details", "request_id"}}`.

## Evaluation

```bash
cd backend
python scripts/run_eval.py                    # deterministic, no DB, no keys; exits 1 on any hard violation
python scripts/run_eval.py --llm configured   # same scenarios through your Gemini/Groq chain
```

The harness builds the knowledge base in memory and runs 31 India scenarios through the real engine.

* Scenarios cover non-Hindu visitors at Lingaraj and Puri, foreign fee caps at Konark, Monday closures, Hinglish
  negation, time budgets, festivals, seasons and synonyms.
* It scores **hard violations against the data** (not the model's words), expectation pass rate, grounding
  rate and latency.
* Each run writes a report to `data/reports/eval_<ts>/`.

## Testing

```bash
cd backend && pytest && ruff check .
TEST_DATABASE_URL=postgresql+psycopg://tourist:change-me@localhost:5432/tourist_test pytest -m integration
cd ../frontend && npm test && npm run lint && npm run build
```

`TEST_DATABASE_URL` must be a **disposable** database: the integration suite recreates its schema.

## Verification status (as delivered)

This was built in a sandbox **without package installation, Docker, PostGIS or Node modules**. Here is exactly
what was run:

| Area | Status |
|---|---|
| Backend domain logic | **237 unit tests run, all passing.** Covers India names/PIN/synonyms, rules, seasons, intent (EN/Hinglish), routing (OSRM via MockTransport + fallback), itinerary, fact grounding, fee extraction, match judge, OSM/Wikipedia/air-quality parsing, Gemini embedder request shape, pipeline, engine. RapidFuzz, Tenacity and pydantic-settings were replaced by small local stand-ins, so re-run after `pip install`. |
| Offline evaluation (mock LLM) | **31/31 scenarios pass, 0 hard violations**, p50 ≈ 12 ms. The 100% grounding rate is from the templated mock LLM; real-model grounding is **not yet measured** — run `--llm configured`. |
| Ruff | clean |
| FastAPI routes, SQL repositories, migration `0002_india`, PostGIS queries | **written, not executed** (FastAPI/SQLAlchemy not installed here). `tests/integration/` covers refine, photo, India ingestion layers and fact governance — run these first. |
| Frontend | type-checked with third-party types stubbed; **`npm install`, Vitest (new India tests included), ESLint and `vite build` not run** |
| Docker Compose, OSRM profile | **not run** |
| Live Google / Overpass / Wikipedia / Gemini / Groq / Open-Meteo calls | **not tested against real services**; shapes follow official docs (Nov 2026) |
| Curated facts and festival dates | researched and sourced on 17 Nov 2026; lunar dates can shift by a day (see `date_note`) — re-verify before relying on them |

## Credentials for live data

| Key | Notes |
|---|---|
| `PLACES_API_KEY` | Google Cloud → *Places API (New)*. Reviews/`goodForChildren` raise the SKU; photos are billed per request |
| `GEMINI_API_KEY` | Google AI Studio — used for ranking, optional intent parsing, fact extraction, match judging, embeddings |
| `GROQ_API_KEY` | fallback LLM |
| none | OSM Overpass, Wikipedia/Wikidata, Open-Meteo (non-commercial) — set `CONTACT_EMAIL` |
| `YELP_API_KEY`, `FLICKR_API_KEY` | optional; Yelp coverage in India is thin, Google photos replace Flickr |

## Known limitations

* The curated layer covers a handful of Odisha places and festivals. Coverage elsewhere relies on unverified,
  reported facts until someone curates them. This is deliberate, so a guess never hides a place.
* Travel times are estimates unless you run OSRM. Temple-area vehicle restrictions aren't modelled.
* Opening hours come from Google's weekday text. Ad-hoc closures (eclipses, rituals, holidays) aren't known.
* The rule intent parser handles common English/Hinglish phrasing only. It does not read Indic scripts, and it
  deliberately does not infer Sikh/Jain/Buddhist visitors as non-Hindu (use the toggle).
* Mock data uses real place names with approximate coordinates and **invented** ratings, reviews, OSM tags and
  Wikipedia-style paraphrases. It is labelled wherever it appears.
* Admin auth is a single shared token.
