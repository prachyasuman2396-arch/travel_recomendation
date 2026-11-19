"""Radius — a day planner for India that picks places from YOUR choices, the weather and the traffic.

Data: Google Maps (ratings, reviews, opening hours, live traffic) when GOOGLE_MAPS_API_KEY is set,
plus OpenStreetMap, Wikipedia and Open-Meteo (free). AI (Gemini / Groq) ranks, explains and summarises
reviews when GEMINI_API_KEY / GROQ_API_KEY are set; without them everything still works.
Keys come from Streamlit secrets or environment variables — never from the page.
"""

from __future__ import annotations

import html
import os
import sys
import traceback
from datetime import datetime, time, timedelta
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

st.set_page_config(page_title="Radius · Plan your day in India", page_icon="🧭", layout="centered",
                   initial_sidebar_state="collapsed")

from app.lite.live_data import LiveDataError  # noqa: E402
from app.lite.router import Keys  # noqa: E402
from app.lite.runtime import (  # noqa: E402
    DEMO_CENTER,
    LiteSettings,
    Statuses,
    background_loop,
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
    "historical": "🏛️ History", "religious": "🛕 Temples", "cultural": "🎭 Culture & museums",
    "nature": "🌿 Nature", "beaches": "🏖️ Beaches", "parks_gardens": "🌳 Parks", "waterfalls": "💧 Waterfalls",
    "wildlife": "🐘 Wildlife", "adventure": "⛰️ Viewpoints", "food": "🍛 Food", "shopping": "🛍️ Markets",
    "photography": "📸 Photo spots", "entertainment": "🎡 Fun",
}
TRAVELLERS = {
    "non_hindu": "Not Hindu", "foreign": "Visiting from abroad", "elderly": "With elderly",
    "kids": "With kids", "wheelchair": "Wheelchair", "crowds": "Avoid crowds",
}
TRAVELLER_HELP = ("“Not Hindu” hides temples where only Hindus may enter (e.g. Jagannath Puri, Lingaraj). "
                  "“Visiting from abroad” uses foreign-visitor ticket prices.")
POPULAR = ["Puri", "Bhubaneswar", "Konark", "Varanasi", "Jaipur", "Agra", "Udaipur", "Hampi", "Mysuru",
           "Kochi", "Rishikesh", "Amritsar", "Darjeeling", "Madurai"]
CATEGORY_ICON = {"religious": "🛕", "historical": "🏛️", "cultural": "🎭", "nature": "🌿", "beaches": "🏖️",
                 "parks_gardens": "🌳", "waterfalls": "💧", "wildlife": "🐘", "adventure": "⛰️", "food": "🍛",
                 "shopping": "🛍️", "entertainment": "🎡"}
AREA_RADII = (5, 10, 15, 25)  # an area is fetched once per bucket, then filtered per request


# =============================================================================== config & keys
def secret(name: str) -> str | None:
    value = os.environ.get(name)
    if not value:
        try:
            value = st.secrets.get(name)  # raises if there is no secrets file at all
        except Exception:
            value = None
    return (str(value).strip() if value else "") or None


SETTINGS = LiteSettings(data_dir=ROOT / "data", contact_email=secret("CONTACT_EMAIL"), osrm_url=secret("OSRM_URL"))
KEYS = Keys(gemini=secret("GEMINI_API_KEY"), groq=secret("GROQ_API_KEY"),
            google=secret("GOOGLE_MAPS_API_KEY") or secret("PLACES_API_KEY"))


@st.cache_resource(show_spinner=False)
def _loop():
    """One event loop + pooled HTTP client for the whole process (survives reruns and sessions)."""
    return background_loop(SETTINGS)


def run(coro, timeout: float = 90.0):
    """Run a coroutine on the shared background loop (never asyncio.run: no connection reuse)."""
    return _loop().run(coro, timeout=timeout)


@st.cache_data(ttl=3600, show_spinner=False)
def _statuses(gemini: str | None, groq: str | None) -> Statuses:
    return run(check_keys(Keys(gemini, groq), SETTINGS))


def statuses() -> Statuses:
    if not KEYS.any:
        return Statuses()
    try:
        return _statuses(KEYS.gemini, KEYS.groq)
    except Exception:  # network trouble: carry on; the router treats unknown providers as unavailable
        return Statuses()


@st.cache_resource(ttl=6 * 3600, max_entries=24, show_spinner=False)
def _area(lat: float, lon: float, radius_km: int, embed_model: str | None, with_google: bool,
          _statuses: Statuses) -> tuple:
    return run(load_area(lat, lon, radius_km, KEYS, _statuses, SETTINGS))


@st.cache_resource(show_spinner=False)
def _demo() -> tuple:
    return run(load_demo_area(SETTINGS))


def get_area(lat: float, lon: float, radius_km: float, demo: bool, sts: Statuses):
    if demo:
        return _demo()
    bucket = next((r for r in AREA_RADII if r >= radius_km), AREA_RADII[-1])
    embed = sts.gemini.embed_model if sts.gemini and sts.gemini.ok else None
    return _area(round(lat, 3), round(lon, 3), bucket, embed, bool(KEYS.google), sts)


# =============================================================================== styling
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');
:root{--ink:#1c1917;--muted:#78716c;--line:#e7e1d8;--card:#ffffff;--bg:#fbf8f3;--brand:#c2410c;
--brand-soft:#fff1e8;--good:#15803d;--good-soft:#ecfdf3;--warn:#b45309;--warn-soft:#fff7e6;--bad:#b91c1c;
--bad-soft:#fef2f2;--blue:#1d4ed8;--blue-soft:#eef4ff}
html,body,[class*="css"],.stMarkdown,button,input,textarea{font-family:'Plus Jakarta Sans',system-ui,sans-serif!important}
#MainMenu,header[data-testid="stHeader"],footer,[data-testid="stToolbar"],[data-testid="stSidebarCollapsedControl"]{display:none!important}
.block-container{padding-top:1.6rem;padding-bottom:4rem;max-width:860px}
.hero{background:linear-gradient(135deg,#c2410c 0%,#ea580c 45%,#f59e0b 100%);color:#fff;border-radius:22px;
padding:28px 28px 24px;margin-bottom:18px;box-shadow:0 12px 30px -12px rgba(194,65,12,.45)}
.hero h1{font-size:2.1rem;font-weight:800;margin:0 0 6px;color:#fff;letter-spacing:-.02em}
.hero p{margin:0;font-size:1.02rem;opacity:.95}
.hero .tags{margin-top:14px;display:flex;gap:8px;flex-wrap:wrap}
.hero .tags span{background:rgba(255,255,255,.18);border:1px solid rgba(255,255,255,.3);padding:4px 11px;
border-radius:999px;font-size:.82rem}
.step{font-size:.78rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--brand);margin:18px 0 2px}
.where{font-size:.92rem;color:var(--muted);margin:2px 0 6px}
.ctx{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:8px 0 14px}
.ctx div{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px 14px}
.ctx b{display:block;font-size:1.05rem;color:var(--ink)}
.ctx small{color:var(--muted);font-size:.78rem}
.note{border-radius:12px;padding:10px 14px;margin:6px 0;font-size:.9rem}
.note.warn{background:var(--warn-soft);border:1px solid #fde4b8;color:#7c2d12}
.note.bad{background:var(--bad-soft);border:1px solid #fecaca;color:#7f1d1d}
.note.info{background:var(--blue-soft);border:1px solid #dbe6ff;color:#1e3a8a}
.card{background:var(--card);border:1px solid var(--line);border-radius:18px;overflow:hidden;margin:14px 0;
box-shadow:0 6px 18px -14px rgba(28,25,23,.35);display:flex}
.card .pic{flex:0 0 210px;min-height:210px;background:#f3ede4 center/cover no-repeat;position:relative}
.card .pic .emoji{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:3.4rem;
background:linear-gradient(135deg,#fde7d6,#fbd5b5)}
.card .rank{position:absolute;top:10px;left:10px;background:#fff;color:var(--brand);font-weight:800;
border-radius:999px;width:32px;height:32px;display:flex;align-items:center;justify-content:center;
box-shadow:0 2px 8px rgba(0,0,0,.15)}
.card .body{padding:16px 18px;flex:1;min-width:0}
.card h3{margin:0;font-size:1.22rem;font-weight:700;color:var(--ink);line-height:1.3}
.card .sub{color:var(--muted);font-size:.86rem;margin:3px 0 8px}
.stars{color:#f59e0b;font-weight:700}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
.chip{font-size:.8rem;padding:4px 10px;border-radius:999px;border:1px solid var(--line);background:#faf7f2;color:#44403c;white-space:nowrap}
.chip.good{background:var(--good-soft);border-color:#bbf7d0;color:var(--good)}
.chip.warn{background:var(--warn-soft);border-color:#fde4b8;color:var(--warn)}
.chip.bad{background:var(--bad-soft);border-color:#fecaca;color:var(--bad)}
.why{font-size:.93rem;color:#292524;margin:6px 0}
.say{background:#faf7f2;border-radius:12px;padding:10px 12px;margin-top:8px;font-size:.88rem}
.say .t{font-weight:700;font-size:.75rem;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin-bottom:4px}
.say .row{margin:3px 0}
.say small{color:var(--muted)}
.links{margin-top:10px;display:flex;gap:8px;flex-wrap:wrap}
.links a{font-size:.84rem;font-weight:600;text-decoration:none;padding:6px 12px;border-radius:10px;
border:1px solid var(--line);color:var(--ink)!important;background:#fff}
.links a.primary{background:var(--brand);border-color:var(--brand);color:#fff!important}
.plan{border-left:3px solid #fed7aa;margin:6px 0 6px 10px;padding-left:16px}
.plan .stop{position:relative;margin:0 0 14px}
.plan .stop:before{content:"";position:absolute;left:-23px;top:4px;width:11px;height:11px;border-radius:50%;
background:var(--brand);box-shadow:0 0 0 3px #fff}
.plan .time{font-weight:700;color:var(--brand);font-size:.86rem}
.plan .name{font-weight:700}
.plan small{color:var(--muted)}
.credit{color:#a8a29e;font-size:.74rem;margin-top:28px;text-align:center}
@media (max-width:640px){.card{flex-direction:column}.card .pic{flex-basis:auto;min-height:170px}
.hero h1{font-size:1.7rem}}
div[data-testid="stForm"]{background:#fff;border:1px solid var(--line);border-radius:18px;padding:18px 18px 8px}
</style>
"""


def esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def note(text: str, kind: str = "info") -> None:
    st.markdown(f'<div class="note {kind}">{text}</div>', unsafe_allow_html=True)


# =============================================================================== inputs
def where(demo: bool) -> tuple[float, float, str] | None:
    st.markdown('<div class="step">1 · Where</div>', unsafe_allow_html=True)
    if demo:
        st.markdown('<div class="where">📍 Bhubaneswar · sample data</div>', unsafe_allow_html=True)
        return DEMO_CENTER[0], DEMO_CENTER[1], "Bhubaneswar (sample data)"
    typed = st.text_input("Where are you starting from?", placeholder="Search a town, city or landmark…",
                          label_visibility="collapsed")
    pick = None if typed.strip() else st.pills("Popular", POPULAR, label_visibility="collapsed")
    query = typed.strip() or (pick or "")
    if not query:
        return None
    if st.session_state.get("last_query") != query:
        try:
            st.session_state.place_options = run(geocode(query, SETTINGS))
        except Exception:
            note("Place search isn’t responding right now. Please try again in a moment.", "warn")
            st.session_state.place_options = []
        st.session_state.last_query = query
    options = st.session_state.get("place_options") or []
    if not options:
        note(f"We couldn’t find “{esc(query)}” in India. Check the spelling or try a nearby town.", "warn")
        return None
    if len(options) > 1:
        p = options[st.selectbox("📍 Which one?", range(len(options)), format_func=lambda i: options[i].label)]
    else:
        p = options[0]
        st.markdown(f'<div class="where">📍 {esc(p.label)}</div>', unsafe_allow_html=True)
    return p.latitude, p.longitude, p.label


def preferences(lat: float, lon: float) -> PreferenceProfile | None:
    st.markdown('<div class="step">2 · Your day</div>', unsafe_allow_html=True)
    with st.form("prefs", border=False):
        interests = st.pills("What do you enjoy?", list(INTERESTS), selection_mode="multi",
                             default=["historical", "religious"], format_func=INTERESTS.get)
        now = datetime.now(IST)
        c1, c2, c3 = st.columns([1.1, 1, 1.3])
        day = c1.date_input("Day", value=now.date(), min_value=now.date(), max_value=now.date() + timedelta(days=15),
                            format="DD/MM/YYYY")
        default_start = time(max(6, min(now.hour + 1, 20)), 0) if day == now.date() else time(9, 0)
        start = c2.time_input("Start", value=default_start, step=timedelta(minutes=30))
        hours = c3.slider("Hours you have", 1.0, 12.0, 4.0, 0.5)
        radius = st.slider("How far will you go? (km)", 2, 25, 10)
        who = st.pills("Travelling", list(TRAVELLERS), selection_mode="multi", format_func=TRAVELLERS.get,
                       help=TRAVELLER_HELP)
        with st.expander("More preferences"):
            text = st.text_area("Anything else?", max_chars=1000, height=80,
                                placeholder="e.g. peaceful places, not too crowded, something for kids… "
                                            "(English or Hinglish)")
            fee_cap = st.number_input("Max ticket price per person (₹, 0 = any)", 0, 100_000, 0, 50)
        submitted = st.form_submit_button("✨ Plan my day", type="primary", use_container_width=True)
    if not submitted:
        return None
    if not interests and not text.strip():
        note("Pick at least one thing you enjoy.", "warn")
        return None
    who = set(who or [])
    return PreferenceProfile(
        latitude=lat, longitude=lon, interests=list(interests or []), natural_language=text.strip() or None,
        max_distance_km=radius, available_hours=hours, client_datetime=datetime.combine(day, start, tzinfo=IST),
        visitor=VisitorProfile(non_hindu_visitor="non_hindu" in who, foreign_national="foreign" in who,
                               wheelchair_user="wheelchair" in who, with_children="kids" in who,
                               with_elderly="elderly" in who, avoid_crowds="crowds" in who,
                               max_entry_fee_inr=int(fee_cap) if fee_cap else None),
    )


# =============================================================================== output
WEATHER_ICON = [("thunder", "⛈️"), ("rain", "🌧️"), ("shower", "🌦️"), ("drizzle", "🌦️"), ("snow", "❄️"),
                ("fog", "🌫️"), ("overcast", "☁️"), ("cloud", "⛅"), ("clear", "☀️")]
TRAFFIC = {"light": ("good", "light traffic"), "moderate": ("warn", "moderate traffic"),
           "heavy": ("bad", "heavy traffic")}
WEATHER_FIT = {"suitable": ("good", "Good weather for it"), "acceptable": ("warn", "Weather so-so"),
               "unsuitable": ("bad", "Poor weather for it")}
OPENING = {"open": "good", "opens_later": "warn", "closed": "bad", "unknown": ""}


def weather_icon(condition: str | None) -> str:
    c = (condition or "").lower()
    return next((icon for key, icon in WEATHER_ICON if key in c), "🌤️")


def context_strip(result: RecommendationResult) -> None:
    boxes = []
    w = result.weather
    if w and w.temperature_c is not None:
        rain = f" · {w.max_precipitation_probability:.0f}% rain" if w.max_precipitation_probability else ""
        boxes.append(f"<div><small>Weather for your plan</small><b>{weather_icon(w.condition)} "
                     f"{w.temperature_c:.0f}°C {esc(w.condition or '')}</b><small>{rain.strip(' ·')}</small></div>")
    aq = result.air_quality
    if aq and aq.us_aqi is not None:
        label = (aq.category or "").replace("_", " ").replace("sensitive", "for sensitive groups")
        boxes.append(f"<div><small>Air quality</small><b>AQI {aq.us_aqi:.0f}</b><small>{esc(label)}</small></div>")
    traffic = [i.travel.traffic for i in result.recommendations if i.travel and i.travel.method == "google_traffic"]
    if traffic:
        worst = "heavy" if "heavy" in traffic else "moderate" if "moderate" in traffic else "light"
        boxes.append(f"<div><small>Traffic at your start time</small><b>🚗 {TRAFFIC[worst][1].title()}</b>"
                     f"<small>live from Google Maps</small></div>")
    if result.season:
        boxes.append(f"<div><small>Season</small><b>🗓️ {esc(result.season)}</b></div>")
    if boxes:
        st.markdown(f'<div class="ctx">{"".join(boxes)}</div>', unsafe_allow_html=True)


def card(item: RecommendationItem) -> str:
    f = item.facts
    icon = CATEGORY_ICON.get(item.category, "📍")
    pic = (f'<div class="pic" style="background-image:url(\'{esc(item.image_url)}\')">'
           if item.image_url else f'<div class="pic"><div class="emoji">{icon}</div>')
    pic += f'<div class="rank">{item.rank}</div></div>'

    sub = [f"{icon} {esc(item.category.replace('_', ' ').title())}"]
    if f.rating is not None:
        sub.insert(0, f'<span class="stars">★ {f.rating:.1f}</span> ({f.rating_count:,} reviews)')
    if item.local_names:
        sub.append(esc(item.local_names[0]))

    chips = []
    if item.travel:
        kind, label = TRAFFIC.get(item.travel.traffic, ("", ""))
        mode = "" if item.travel.method == "google_traffic" else " (est.)"
        chips.append(f'<span class="chip {kind}">🚗 {item.travel.minutes} min{mode}'
                     f'{" · " + label if label else ""}</span>')
    else:
        chips.append(f'<span class="chip">📍 {f.distance_km:.1f} km</span>')
    if item.opening and item.opening.status != "unknown":
        chips.append(f'<span class="chip {OPENING[item.opening.status]}">🕒 {esc(item.opening.label)}</span>')
    if item.weather_suitability in WEATHER_FIT:
        kind, label = WEATHER_FIT[item.weather_suitability]
        chips.append(f'<span class="chip {kind}">{label}</span>')

    notices = "".join(
        f'<div class="note {"bad" if n.severity == "critical" else "warn"}">{esc(n.text)}</div>'
        for n in item.notices if n.severity in ("critical", "warning") and n.kind != "accessibility")
    fee = next((n.text for n in item.notices if n.kind == "fee"), None)
    if fee:
        chips.append(f'<span class="chip">🎟️ {esc(fee)}</span>')

    say = ""
    rs = item.review_summary
    if rs:
        rows = []
        if rs.likes:
            rows.append(f'<div class="row">👍 {esc(", ".join(rs.likes))}</div>')
        if rs.dislikes:
            rows.append(f'<div class="row">👎 {esc(", ".join(rs.dislikes))}</div>')
        source = "Google " if "google_places" in f.rating_sources else ""
        how = " · summarised by AI" if rs.method == "ai" else ""
        say = (f'<div class="say"><div class="t">What visitors say</div>{"".join(rows)}'
               f'<small>From {rs.based_on} recent {source}review{"s" if rs.based_on != 1 else ""}{how}</small>'
               f'</div>')

    hours = (f'<div class="sub">Hours today: {esc(item.opening.hours_today)}</div>'
             if item.opening and item.opening.hours_today else "")
    links = [f'<a class="primary" href="{esc(item.maps_url)}" target="_blank">Directions</a>'] if item.maps_url else []
    if item.wiki_url:
        links.append(f'<a href="{esc(item.wiki_url)}" target="_blank">About this place</a>')
    if item.website:
        links.append(f'<a href="{esc(item.website)}" target="_blank">Website</a>')

    return (f'<div class="card">{pic}<div class="body"><h3>{esc(item.name)}</h3>'
            f'<div class="sub">{" · ".join(sub)}</div><div class="chips">{"".join(chips)}</div>{hours}'
            f'<div class="why">{esc(item.reason)}</div>{notices}{say}'
            f'<div class="links">{"".join(links)}</div></div></div>')


def day_plan(result: RecommendationResult) -> None:
    it = result.itinerary
    if not it or not it.stops:
        note("No timed plan fits the hours you have — try more time or a smaller area.", "info")
        return
    traffic = "with live traffic" if it.travel_method == "google_traffic" else "estimated drive times"
    st.markdown(f"**{it.start:%H:%M} → {it.end:%H:%M}** · about {it.total_travel_minutes} min on the road "
                f"({traffic})")
    stops = []
    for s in it.stops:
        extra = " · waits for opening" if s.wait_minutes else ""
        stops.append(f'<div class="stop"><div class="time">{s.arrive:%H:%M}</div><div class="name">{esc(s.name)}</div>'
                     f'<small>{s.visit_minutes} min visit · {s.travel_minutes} min drive before{extra}</small></div>')
    st.markdown(f'<div class="plan">{"".join(stops)}</div>', unsafe_allow_html=True)
    if it.skipped:
        st.caption("Didn’t fit in your time: " + ", ".join(it.skipped))


def show_result(result: RecommendationResult) -> None:
    st.markdown('<div class="step">3 · Your picks</div>', unsafe_allow_html=True)
    context_strip(result)
    for ev in result.events:
        note(f"🎉 <b>{esc(ev.name)}</b> ({ev.start:%d %b}) near {esc(ev.location_name)} — {esc(ev.description)}",
             "warn")
    if result.request.visitor.non_hindu_visitor and result.excluded.get("entry_restricted"):
        note("Temples where only Hindus may enter are left out of your picks.", "info")
    if not result.recommendations:
        note("Nothing fits yet. Try a larger distance, more time or another interest.", "warn")
        return
    tabs = st.tabs(["Places", "Day plan", "Map"])
    with tabs[0]:
        st.markdown("".join(card(i) for i in result.recommendations), unsafe_allow_html=True)
    with tabs[1]:
        day_plan(result)
    with tabs[2]:
        import pandas as pd

        points = pd.DataFrame([{"lat": i.latitude, "lon": i.longitude, "color": "#c2410c", "size": 70}
                               for i in result.recommendations]
                              + [{"lat": result.request.latitude, "lon": result.request.longitude,
                                  "color": "#1d4ed8", "size": 90}])
        st.map(points, latitude="lat", longitude="lon", color="color", size="size")
        st.caption("🟠 your picks · 🔵 where you start")


def refine_box(store, sts: Statuses) -> None:
    with st.form("refine", clear_on_submit=True, border=False):
        c1, c2 = st.columns([4, 1])
        msg = c1.text_input("Not quite right?", placeholder="fewer temples · only 2 hours · something for kids",
                            label_visibility="collapsed")
        go = c2.form_submit_button("Update", use_container_width=True)
    if go and msg.strip():
        with st.spinner("Updating your picks…"):
            try:
                profile, changes, result = run(refine(store, st.session_state.profile, msg.strip(), KEYS, sts,
                                                      SETTINGS))
            except Exception:
                note("Couldn’t apply that change. Try saying it differently.", "warn")
                return
        st.session_state.update(profile=profile, changes=changes, result=result)
        st.rerun()
    if st.session_state.get("changes"):
        st.caption("Updated: " + " · ".join(st.session_state.changes))


# =============================================================================== page
def main() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(
        '<div class="hero"><h1>🧭 Radius</h1><p>Places that fit your day — chosen around what you love, '
        'the weather and the traffic.</p><div class="tags"><span>⭐ Real ratings &amp; reviews</span>'
        '<span>🌦️ Weather for your time</span><span>🚗 Live traffic</span><span>🕒 Opening hours</span>'
        '</div></div>', unsafe_allow_html=True)
    for k, v in {"result": None, "profile": None, "changes": None, "area_key": None, "place_options": []}.items():
        st.session_state.setdefault(k, v)
    demo = st.query_params.get("demo") == "1"  # developer-only sample data: /?demo=1
    sts = statuses()

    loc = where(demo)
    if loc is None:
        return
    lat, lon, _ = loc
    profile = preferences(lat, lon)
    if profile is not None:
        try:
            with st.spinner("Finding places, reviews and weather…"):
                store, _ = get_area(lat, lon, profile.max_distance_km, demo, sts)
            with st.spinner("Checking traffic and ranking your picks…"):
                result, _ = run(recommend(store, profile, KEYS, sts, SETTINGS))
        except LiveDataError as exc:
            note(esc(str(exc)), "warn")
            return
        except Exception as exc:
            note("Something went wrong while planning your day. Please try again.", "bad")
            if st.query_params.get("debug") == "1":
                st.code("".join(traceback.format_exception(exc))[-3000:])
            return
        st.session_state.update(result=result, profile=profile, changes=None,
                                area_key=(round(lat, 3), round(lon, 3), demo))
    result = st.session_state.get("result")
    if result is None or st.session_state.area_key is None:
        return
    if st.session_state.area_key[:2] != (round(lat, 3), round(lon, 3)):
        return
    show_result(result)
    try:
        store, _ = get_area(lat, lon, st.session_state.profile.max_distance_km, demo, sts)
        refine_box(store, sts)
    except Exception:
        pass  # refinement is optional; results are already on screen
    sources = "Google Maps · " if KEYS.google else ""
    st.markdown(f'<div class="credit">Data: {sources}OpenStreetMap contributors · Wikipedia (CC BY-SA) · '
                'Open-Meteo. Rules, prices and timings change — check locally before you go.</div>',
                unsafe_allow_html=True)


main()
