"""Radius — AI tourist recommender for India (Streamlit edition).

Runs on Streamlit Community Cloud and on Render with no database:
  * places: OpenStreetMap + Wikipedia + Open-Meteo (free, no keys)
  * AI: Gemini and Groq, routed per task, each the other's fallback
Add GEMINI_API_KEY and/or GROQ_API_KEY (secrets, env vars, or the sidebar) and it works.
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

import streamlit as st  # noqa: E402

st.set_page_config(page_title="Radius · India travel planner", page_icon="🧭", layout="wide")

from app.lite.discovery import ProviderStatus  # noqa: E402
from app.lite.live_data import LiveDataError, LiveReport  # noqa: E402
from app.lite.router import TASK_LABELS, Keys  # noqa: E402
from app.lite.runtime import (  # noqa: E402
    DEMO_CENTER,
    LiteSettings,
    Statuses,
    check_keys,
    geocode,
    load_area,
    load_demo_area,
    recommend,
    refine,
)
from app.schemas.preference import PreferenceProfile  # noqa: E402
from app.schemas.recommendation import RecommendationItem, RecommendationResult  # noqa: E402
from app.services.india.rules import VisitorProfile  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
INTERESTS = {
    "historical": "History & monuments", "religious": "Temples & shrines", "cultural": "Culture & museums",
    "nature": "Nature", "parks_gardens": "Parks & gardens", "waterfalls": "Waterfalls", "beaches": "Beaches",
    "wildlife": "Wildlife", "adventure": "Viewpoints & adventure", "food": "Food", "shopping": "Markets",
    "photography": "Photography", "entertainment": "Entertainment",
}
POPULAR = ["Bhubaneswar", "Puri", "Konark", "Varanasi", "Jaipur", "Agra", "Udaipur", "Delhi", "Mumbai", "Hampi",
           "Mysuru", "Madurai", "Kochi", "Rishikesh", "Amritsar", "Darjeeling", "Shillong", "Leh"]
AREA_RADII = (5, 10, 15, 25)  # area is fetched once per bucket, then filtered per request


# --------------------------------------------------------------------------- config & keys
def secret(name: str) -> str | None:
    value = os.environ.get(name)
    if not value:
        try:
            value = st.secrets.get(name)  # raises if there is no secrets file at all
        except Exception:
            value = None
    value = (str(value).strip() if value else "") or None
    return value


SETTINGS = LiteSettings(data_dir=ROOT / "data", contact_email=secret("CONTACT_EMAIL"), osrm_url=secret("OSRM_URL"))


def run(coro):
    """Run a coroutine from Streamlit's script thread."""
    return asyncio.run(coro)


def current_keys() -> Keys:
    return Keys(gemini=st.session_state.get("gemini_key") or None, groq=st.session_state.get("groq_key") or None)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_statuses(gemini: str | None, groq: str | None) -> Statuses:
    return run(check_keys(Keys(gemini, groq), SETTINGS))


def statuses() -> Statuses:
    keys = current_keys()
    if not keys.any:
        return Statuses()
    try:
        return cached_statuses(keys.gemini, keys.groq)
    except Exception as exc:  # network trouble: keep going, the router treats unknown as unavailable
        st.sidebar.warning(f"Could not check keys right now: {exc}")
        return Statuses()


@st.cache_resource(ttl=6 * 3600, max_entries=24, show_spinner=False)
def cached_area(lat: float, lon: float, radius_km: int, embed_model: str | None, deep: bool,
                _keys: Keys, _statuses: Statuses) -> tuple:
    return run(load_area(lat, lon, radius_km, _keys, _statuses, SETTINGS, deep_facts=deep))


@st.cache_resource(show_spinner=False)
def cached_demo() -> tuple:
    return run(load_demo_area(SETTINGS))


def init_state() -> None:
    defaults = {"gemini_key": secret("GEMINI_API_KEY"), "groq_key": secret("GROQ_API_KEY"),
                "keys_from_secrets": bool(secret("GEMINI_API_KEY") or secret("GROQ_API_KEY")),
                "place": None, "place_options": [], "result": None, "profile": None, "plan": None,
                "changes": None, "area_key": None}
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)


# --------------------------------------------------------------------------- sidebar
def sidebar() -> Statuses:
    with st.sidebar:
        st.markdown("## 🧭 Travel Companion")
        st.caption("AI-powered day planner for destinations across India.")

        st.markdown("---")
        st.markdown("### 💡 Travel Tips for India")
        st.markdown(
            "• **Temple Decorum**: Remove shoes at the entrance, dress modestly covering shoulders and knees.\n"
            "• **Best Timing**: Visit iconic shrines and monuments between 6:00 AM – 9:30 AM to beat crowds and heat.\n"
            "• **Hydration**: Carry bottled water, especially when exploring open monuments and beaches.\n"
            "• **Local Transport**: Auto-rickshaws and app cabs are widely available in most Indian cities."
        )

        st.markdown("---")
        with st.expander("⚙️ Advanced Settings", expanded=False):
            st.caption("AI Keys are automatically loaded from `.env`. You can also enter custom keys here:")
            g = st.text_input("Gemini API key", value=st.session_state.gemini_key or "", type="password")
            q = st.text_input("Groq API key", value=st.session_state.groq_key or "", type="password")
            if st.button("Save custom keys"):
                st.session_state.gemini_key = g.strip() or None
                st.session_state.groq_key = q.strip() or None
                st.rerun()

        st.caption("Places & maps © OpenStreetMap contributors · Background text from Wikipedia")
        return statuses()


# --------------------------------------------------------------------------- inputs
def where() -> tuple[float, float, str, bool] | None:
    st.subheader("Where are you?")
    demo = st.toggle("Use offline demo data (Bhubaneswar sample)", value=False,
                     help="No internet services needed. Ratings and reviews in the demo are invented.")
    if demo:
        return DEMO_CENTER[0], DEMO_CENTER[1], "Bhubaneswar (demo data)", True
    c1, c2 = st.columns([3, 1])
    with c1:
        typed = st.text_input("Town, city or landmark in India", value="",
                              placeholder="e.g. Puri, Hampi, Old Delhi, Mahabalipuram")
    with c2:
        pick = st.selectbox("…or a popular place", ["—", *POPULAR], index=0)
    query = typed.strip() or (pick if pick != "—" else "")
    if not query:
        st.caption("Type a place or pick one to begin.")
        return None
    if st.session_state.get("last_query") != query:
        try:
            st.session_state.place_options = run(geocode(query, SETTINGS))
        except Exception as exc:
            st.error(f"Place search is unavailable right now ({exc}). Try again in a moment.")
            st.session_state.place_options = []
        st.session_state.last_query = query
    options = st.session_state.place_options
    if not options:
        st.warning(f"No place in India found for “{query}”. Check the spelling or try a nearby town.")
        return None
    labels = [p.label for p in options]
    idx = st.selectbox("Did you mean", range(len(labels)), format_func=lambda i: labels[i]) if len(options) > 1 else 0
    p = options[idx]
    return p.latitude, p.longitude, p.label, False


def preferences(lat: float, lon: float) -> PreferenceProfile | None:
    st.subheader("Plan your day")
    with st.form("prefs"):
        c1, c2 = st.columns(2)
        with c1:
            interests = st.multiselect("What do you enjoy?", list(INTERESTS), default=["historical", "religious"],
                                       format_func=INTERESTS.get)
            radius = st.slider("How far will you go? (km)", 2, 25, 10)
            hours = st.slider("Time available (hours)", 1.0, 12.0, 4.0, 0.5)
        with c2:
            now = datetime.now(IST)
            day = st.date_input("Date", value=now.date(), min_value=date(2024, 1, 1))
            start = st.time_input("Start time (IST)", value=time(max(6, min(now.hour + 1, 20)), 0))
            text = st.text_area("Anything else? English or Hinglish is fine", height=96, max_chars=1000,
                                placeholder="Purane mandir aur shant jagah, bheed kam. No shopping.")
        st.markdown("**Who's travelling?**")
        v1, v2, v3 = st.columns(3)
        non_hindu = v1.checkbox("Not Hindu", help="Some temples admit Hindus only (e.g. Lingaraj, Jagannath Puri)")
        foreign = v1.checkbox("Foreign national", help="Many monuments charge foreign visitors more")
        elderly = v2.checkbox("With elderly people")
        kids = v2.checkbox("With children")
        wheelchair = v3.checkbox("Wheelchair user")
        crowds = v3.checkbox("Avoid crowds")
        fee_cap = st.number_input("Highest entry fee per person, ₹ (0 = no limit)", 0, 100_000, 0, 50)
        submitted = st.form_submit_button("Find places", type="primary")
    if not submitted:
        return None
    if not interests and not text.strip():
        st.warning("Pick at least one interest or describe what you'd like to do.")
        return None
    return PreferenceProfile(
        latitude=lat, longitude=lon, interests=interests, natural_language=text.strip() or None,
        max_distance_km=radius, available_hours=hours,
        client_datetime=datetime.combine(day, start, tzinfo=IST),
        visitor=VisitorProfile(non_hindu_visitor=non_hindu, foreign_national=foreign, wheelchair_user=wheelchair,
                               with_children=kids, with_elderly=elderly, avoid_crowds=crowds,
                               max_entry_fee_inr=int(fee_cap) if fee_cap else None),
    )


# --------------------------------------------------------------------------- data + AI
def get_area(lat: float, lon: float, radius_km: float, demo: bool, sts: Statuses):
    if demo:
        return cached_demo()
    bucket = next((r for r in AREA_RADII if r >= radius_km), AREA_RADII[-1])
    keys = current_keys()
    embed_model = sts.gemini.embed_model if sts.gemini and sts.gemini.ok else None
    return cached_area(round(lat, 3), round(lon, 3), bucket, embed_model, bool(st.session_state.get("deep_facts")),
                       keys, sts)


def show_area_report(report: LiveReport) -> None:
    st.caption(f"📍 Loaded **{report.places} destinations** · Live ratings, reviews, opening hours & weather ready")


def method_line(result: RecommendationResult) -> str:
    if result.generation_method == "llm" and result.provider:
        return f"✨ AI-Curated & Ranked ({result.provider.title()})"
    return "✨ AI-Curated Day Plan & Recommendations"


# --------------------------------------------------------------------------- output
SEVERITY = {"critical": st.error, "warning": st.warning}


def show_item(item: RecommendationItem) -> None:
    with st.container(border=True):
        left, right = st.columns([1, 3]) if item.image_url else (None, st.container())
        if left is not None:
            with left:
                st.image(item.image_url)
                if item.image_attribution:
                    st.caption(item.image_attribution)
        with right:
            local = f" · {item.local_names[0]}" if item.local_names else ""
            st.markdown(f"### {item.rank}. {item.name}{local}")
            f = item.facts
            meta = [f"{f.distance_km:.1f} km", item.category.replace("_", " ")]
            if f.hours_today:
                meta.append(f"today: {f.hours_today}")
            if f.rating is not None:
                meta.append(f"★ {f.rating:.1f} ({f.rating_count:,} reviews)")
            st.caption(" · ".join(meta))
            for n in sorted(item.notices, key=lambda n: {"critical": 0, "warning": 1}.get(n.severity, 2)):
                src = f" [source]({n.source_url})" if n.source_url and n.verified else ""
                tag = "" if n.verified else " _(reported, not verified)_"
                # Red is reserved for verified rules; anything only "reported" is at most a warning.
                level = n.severity if n.verified else ("warning" if n.severity == "critical" else n.severity)
                (SEVERITY.get(level) or st.info)(f"{n.text}{tag}{src}")
            st.write(item.reason)
            if item.season_note:
                st.caption(item.season_note)
            if item.advantages or item.considerations:
                a, c = st.columns(2)
                with a:
                    for x in item.advantages[:3]:
                        st.markdown(f"✅ {x}")
                with c:
                    for x in item.considerations[:3]:
                        st.markdown(f"⚠️ {x}")
            if item.cited_facts:
                with st.expander(f"Based on {len(item.cited_facts)} sourced fact(s)"):
                    for fact in item.cited_facts:
                        flag = "" if fact.verified else " (unverified)"
                        st.markdown(f"- {fact.text} — [{fact.source_name or 'source'}]({fact.source_url}){flag}")
            links = [f"[Directions](https://www.openstreetmap.org/directions?to={item.latitude}%2C{item.longitude})"]
            if item.wiki_url:
                links.append(f"[Wikipedia]({item.wiki_url})")
            if item.website:
                links.append(f"[Website]({item.website})")
            st.markdown(" · ".join(links))


def show_result(result: RecommendationResult) -> None:
    top = [method_line(result)]
    if result.season:
        top.append(result.season)
    if result.weather and result.weather.temperature_c is not None:
        top.append(f"{result.weather.temperature_c:.0f}°C {result.weather.condition or ''}".strip())
    if result.air_quality and result.air_quality.us_aqi is not None:
        top.append(f"AQI {result.air_quality.us_aqi:.0f} ({(result.air_quality.category or '').replace('_', ' ')})")
    st.caption(" · ".join(x for x in top if x))
    if result.understood:
        st.markdown("**We understood:** " + " · ".join(f"`{u}`" for u in result.understood))
    if result.data_origin == "mock":
        st.info("Demo data: place names are real, but ratings and reviews are invented.")
    for ev in result.events:
        st.warning(f"**{ev.name}** ({ev.start:%d %b}–{ev.end:%d %b}, {ev.location_name}): {ev.description} "
                   f"[source]({ev.source_url})")
    if result.summary:
        st.write(result.summary)
    if not result.recommendations:
        st.warning("Nothing suitable found. Try a larger distance, more interests or a different time.")
        return
    tabs = st.tabs(["Places", "Plan", "Map"])
    with tabs[0]:
        for item in result.recommendations:
            show_item(item)
    with tabs[1]:
        it = result.itinerary
        if it and it.stops:
            label = "estimated" if it.travel_method == "estimate" else "road network"
            st.markdown(f"**{it.start:%H:%M} – {it.end:%H:%M}** · {it.total_travel_minutes} min travelling "
                        f"({label})")
            st.table([{"Arrive": f"{s.arrive:%H:%M}", "Place": s.name, "Visit": f"{s.visit_minutes} min",
                       "Travel": f"{s.travel_minutes} min",
                       "Note": ("waits for opening" if s.wait_minutes else "") + ("" if s.hours_known
                                                                                  else " hours unknown")}
                      for s in it.stops])
            for n in it.notes:
                st.caption(n)
            if it.skipped:
                st.caption("Didn't fit: " + ", ".join(it.skipped))
        else:
            st.caption("No timed plan fits the time you have." if it else "No plan available.")
    with tabs[2]:
        import pandas as pd

        points = pd.DataFrame([{"lat": i.latitude, "lon": i.longitude, "color": "#c2410c"}
                               for i in result.recommendations]
                              + [{"lat": result.request.latitude, "lon": result.request.longitude,
                                  "color": "#1d4ed8"}])
        st.map(points, latitude="lat", longitude="lon", color="color", size=60)
        st.caption("Orange: recommended places · Blue: your starting point")
    skipped = {k: v for k, v in result.excluded.items() if v}
    if skipped:
        st.caption("Skipped: " + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in skipped.items()))
    for w in result.warnings:
        st.caption(f"ℹ️ {w}")


def refine_box(store, sts: Statuses) -> None:
    st.divider()
    with st.form("refine", clear_on_submit=True):
        msg = st.text_input("Change something", placeholder="fewer temples · only 2 hours · kuch khana bhi")
        go = st.form_submit_button("Update results")
    if go and msg.strip():
        with st.spinner("Updating…"):
            try:
                profile, changes, result = run(refine(store, st.session_state.profile, msg.strip(), current_keys(),
                                                      sts, SETTINGS))
            except Exception as exc:
                st.error(f"Couldn't apply that change: {exc}")
                return
        st.session_state.profile, st.session_state.changes, st.session_state.result = profile, changes, result
        st.rerun()
    if st.session_state.changes:
        st.caption("Updated: " + ", ".join(st.session_state.changes))


# --------------------------------------------------------------------------- page
def main() -> None:
    init_state()
    sts = sidebar()
    st.title("🧭 Radius — places that fit your day in India")
    st.caption("Real places from OpenStreetMap, checked against opening hours, temple entry rules, festival "
               "crowds, the season and air quality — explained by AI with sources.")
    loc = where()
    if loc is None:
        return
    lat, lon, label, demo = loc
    st.caption(f"📍 {label} ({lat:.4f}, {lon:.4f})")
    profile = preferences(lat, lon)
    if profile is not None:
        try:
            with st.status("Working on it…", expanded=False) as box:
                box.update(label="Finding places, community reviews & weather…")
                store, report = get_area(lat, lon, profile.max_distance_km, demo, sts)
                box.update(label="Ranking places…")
                result, plan = run(recommend(store, profile, current_keys(), sts, SETTINGS))
                box.update(label="Done", state="complete")
        except LiveDataError as exc:
            st.error(str(exc))
            return
        except Exception as exc:
            st.error("Something went wrong while building your recommendations. Please try again.")
            with st.expander("Technical details"):
                st.code("".join(traceback.format_exception(exc))[-3000:])
            return
        st.session_state.update(result=result, profile=profile, plan=plan, changes=None,
                                area_key=(round(lat, 3), round(lon, 3), demo))
        st.session_state.report = report
    result = st.session_state.get("result")
    if result is None or st.session_state.area_key is None:
        return
    if st.session_state.area_key[:2] != (round(lat, 3), round(lon, 3)):
        st.caption("Change your inputs and press “Find places” to see results for this location.")
        return
    if st.session_state.get("report"):
        show_area_report(st.session_state.report)
    show_result(result)
    try:
        store, _ = get_area(lat, lon, st.session_state.profile.max_distance_km, demo, sts)
        refine_box(store, sts)
    except Exception:
        pass  # refinement is optional; results are already on screen


main()
