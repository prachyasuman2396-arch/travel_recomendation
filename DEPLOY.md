# Deploy the Streamlit app (free) — Streamlit Community Cloud or Render

The Streamlit edition (`streamlit_app.py`) needs **no database and no paid API**:

| Need | Source | Key? |
|---|---|---|
| Places, local names, opening hours, wheelchair tags, restaurants, markets | OpenStreetMap (Overpass, 3 public servers with automatic fail-over) | none |
| Background text and photos | Wikipedia / Wikidata / Wikimedia Commons | none |
| Weather and air quality | Open-Meteo | none |
| Place search ("Hampi", "Old Delhi") | Open-Meteo geocoding | none |
| Temple rules, ticket prices, festivals (verified) | `data/curated/*.json` (sourced) | none |
| AI understanding, ranking, explanations | **Gemini** and/or **Groq** | free keys |

## 1. Get the free AI keys (2 minutes each)

* **Gemini**: https://aistudio.google.com → *Get API key* → *Create API key*.
* **Groq**: https://console.groq.com → *API Keys* → *Create API Key*.

One key is enough. With both, each task goes to the better-suited model and the other is the automatic
fallback:

| Task | First | Fallback |
|---|---|---|
| Understanding your request / "fewer temples" refinements | Groq (fast model) | Gemini |
| Ranking + cited explanations | Gemini | Groq (large model) |
| Deep fact extraction from Wikipedia (optional toggle) | Gemini | Groq |
| Semantic matching ("peaceful lake") | Gemini embeddings | keyword matching (TF-IDF) |

The app **asks each provider which models your key can use** and picks the newest suitable one. You never
have to edit a model name, and a renamed or retired model doesn't break it. If both providers fail, for
example on a rate limit, results still appear, ranked by rules and clearly labelled.

## 2a. Streamlit Community Cloud

1. Push this folder to a GitHub repository (public or private).
2. Go to https://share.streamlit.io, choose **Create app → Deploy a public app from GitHub**, and pick the repo.
3. Set the main file path to `streamlit_app.py`. Under **Advanced settings**, set Python to **3.12**.
4. In **Secrets**, paste:
   ```toml
   GEMINI_API_KEY = "your-gemini-key"
   GROQ_API_KEY = "your-groq-key"
   CONTACT_EMAIL = "you@example.com"
   ```
5. Click **Deploy**. The first build takes a few minutes, because it installs pandas and scikit-learn.

## 2b. Render

1. Push the repo to GitHub.
2. In Render, choose **New → Blueprint** and pick the repo. Render reads `render.yaml`.
3. When prompted, enter `GEMINI_API_KEY`, `GROQ_API_KEY` and, optionally, `CONTACT_EMAIL`. Click **Apply**.
4. Open the `onrender.com` URL once the deploy is live. The health check is `/_stcore/health`.

Without Blueprints, create a **Web Service** with these settings:

* **Build:** `pip install -r requirements.txt`
* **Start:** `streamlit run streamlit_app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true`
* **Env vars:** `PYTHON_VERSION=3.12.8` plus the keys

The free Render plan sleeps when idle, so the first visit after a while takes ~30–60 s to wake.

## 3. Run it on your computer

```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml # then paste your keys
streamlit run streamlit_app.py
```

You can also skip the secrets file and paste the keys into the sidebar. Sidebar keys last for that browser
session only.

## What to expect

* **First search in a new area** takes ~10–60 s: OpenStreetMap plus up to 40 Wikipedia articles. The area is
  then cached for 6 hours, so later searches there take a few seconds.
* **Coverage** depends on OpenStreetMap tagging:
  * Big cities and heritage towns are well covered.
  * Small towns may have few places with opening hours. Missing hours show as "hours unknown" and never hide
    a place.
* **No ratings or reviews** in live mode. Free sources don't have them, and the app never invents them. The
  offline *demo* toggle uses sample data, with invented ratings that are labelled as such.
* **Verified temple rules, fees and festivals** currently cover Odisha (Lingaraj, Jagannath Puri, Konark).
  Elsewhere, facts are "Reported", taken automatically from Wikipedia with the quote kept. To add verified
  facts for your city, add entries with a `source_url` to `data/curated/poi_facts.json`.
* **If OpenStreetMap is busy** (public servers sometimes are), the app tries three servers. If all are busy,
  it says so; try again or use a smaller radius.
* **Free API limits**: Gemini's and Groq's free tiers limit requests per minute and per day. One search uses
  1–3 AI calls.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Sidebar shows 🔴 "rejected this API key" | The key was copied incompletely or revoked; create a new one |
| "Ranked by rules (no AI available)" | Both providers failed or are rate-limited; wait a minute, or add the second key |
| "OpenStreetMap servers are busy" | Retry in a minute or reduce the distance slider |
| Streamlit Cloud build fails on Python 3.13+ | Choose Python 3.12 in Advanced settings and redeploy |
