"""Radius — a day planner for India that picks places from YOUR choices, the weather and the traffic.

Data: Google Maps (ratings, reviews, opening hours, live traffic) when GOOGLE_MAPS_API_KEY is set,
plus OpenStreetMap, Wikipedia and Open-Meteo (free). AI (Gemini / Groq) ranks, explains and summarises
reviews when GEMINI_API_KEY / GROQ_API_KEY are set; without them everything still works.
Keys come from Streamlit secrets or environment variables — never from the page.
"""

from __future__ import annotations

import html
import itertools
import json
import math
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

import pydeck as pdk  # noqa: E402
import streamlit as st  # noqa: E402
import streamlit.components.v1 as components  # noqa: E402

st.set_page_config(page_title="Radius · A day that fits, anywhere in India", page_icon=":material/explore:", layout="centered",
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
from app.services.india.facts import DEFAULT_VISIT_MINUTES  # noqa: E402
from app.services.india.rules import VisitorProfile  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
INTERESTS = {
    "historical": "History", "religious": "Temples", "cultural": "Culture & museums", "nature": "Nature",
    "beaches": "Beaches", "parks_gardens": "Parks", "waterfalls": "Waterfalls", "wildlife": "Wildlife",
    "adventure": "Viewpoints", "food": "Food", "shopping": "Markets", "photography": "Photo spots",
    "entertainment": "Leisure",
}
TRAVELLERS = {
    "non_hindu": "Not Hindu", "foreign": "Visiting from abroad", "elderly": "With elderly",
    "kids": "With kids", "wheelchair": "Wheelchair", "crowds": "Avoid crowds",
}
TRAVELLER_HELP = ("“Not Hindu” hides temples where only Hindus may enter (e.g. Jagannath Puri, Lingaraj). "
                  "“Visiting from abroad” uses foreign-visitor ticket prices.")
POPULAR = ["Puri", "Bhubaneswar", "Konark", "Varanasi", "Jaipur", "Agra", "Udaipur", "Hampi", "Mysuru",
           "Kochi", "Rishikesh", "Amritsar", "Darjeeling", "Madurai"]
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


# =============================================================================== styling (DESIGN.md v3 "Peacock & Marigold")
# One hue per kind of place: harmonised, earthy-vivid, used on card art, tags, map pillars and plan markers.
CATEGORY_COLOR = {
    "religious": "#E07A1F", "historical": "#A0522D", "cultural": "#B83B5E", "nature": "#2E8B57",
    "parks_gardens": "#5A9E3A", "beaches": "#1C8DA6", "waterfalls": "#2F6FB0", "wildlife": "#7A8F2B",
    "adventure": "#C9862B", "food": "#C0392B", "shopping": "#8E44AD", "entertainment": "#3F51B5",
}
CATEGORY_NAME = {
    "religious": "Temple", "historical": "Heritage", "cultural": "Culture", "nature": "Nature",
    "parks_gardens": "Garden", "beaches": "Beach", "waterfalls": "Waterfall", "wildlife": "Wildlife",
    "adventure": "Viewpoint", "food": "Food", "shopping": "Market", "entertainment": "Leisure",
}
INDOOR = {"cultural", "shopping", "entertainment", "food"}
CITY_COORDS = {  # globe pins for the popular list
    "Puri": (19.81, 85.83), "Bhubaneswar": (20.30, 85.82), "Konark": (19.89, 86.09), "Varanasi": (25.32, 83.01),
    "Jaipur": (26.91, 75.79), "Agra": (27.18, 78.01), "Udaipur": (24.59, 73.71), "Hampi": (15.34, 76.46),
    "Mysuru": (12.30, 76.64), "Kochi": (9.93, 76.27), "Rishikesh": (30.09, 78.27), "Amritsar": (31.63, 74.87),
    "Darjeeling": (27.04, 88.26), "Madurai": (9.93, 78.12),
}

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600;9..144,700&family=Hanken+Grotesk:wght@400;500;600;700&family=IBM+Plex+Mono:wght@500;600&display=swap');
:root{
--sand:#FBF7F1;--sand-2:#F4EDE2;--surface:#FFFFFF;--line:#ECE4D8;--line-2:#E2D8C9;
--ink:#1F2A2E;--ink2:#56636A;--ink3:#657276;
--peacock:#0F5C63;--peacock-2:#13777F;--peacock-deep:#0B3F45;--peacock-tint:#E3F0EF;
--marigold:#F2A33A;--marigold-tint:#FDF1DC;--terracotta:#B54D2A;
--success:#1E8A5A;--warning:#C77A12;--danger:#C63D2F;--neutral:#93A0A3;
--shadow:0 1px 2px rgba(31,42,46,.06),0 8px 24px -14px rgba(31,42,46,.28);
--shadow-hi:0 2px 4px rgba(31,42,46,.06),0 22px 40px -18px rgba(11,63,69,.40);
--ease:cubic-bezier(.2,.8,.2,1);
--serif:"Fraunces",Georgia,serif;--sans:"Hanken Grotesk",-apple-system,"Segoe UI",system-ui,sans-serif;
--mono:"IBM Plex Mono",ui-monospace,Menlo,monospace}
html,body,.stApp{color:var(--ink);background:var(--sand)!important}
.stApp,.stApp p,.stApp label,.stApp input,.stApp textarea,.stApp button,.stMarkdown{font-family:var(--sans)!important}
#MainMenu,footer,header[data-testid="stHeader"],[data-testid="stToolbar"],[data-testid="stDecoration"],
[data-testid="stSidebarCollapsedControl"],[data-testid="stElementToolbar"]{display:none!important}
.block-container{max-width:880px;padding:20px 20px 72px!important}
:focus-visible{outline:2px solid var(--peacock)!important;outline-offset:2px;border-radius:6px}
iframe{border-radius:22px}

.step{display:flex;align-items:baseline;gap:10px;margin:36px 0 12px}
.step b{font:600 11px/16px var(--mono);letter-spacing:.1em;color:var(--peacock)}
.step span{font:600 22px/28px var(--serif);letter-spacing:-.01em;color:var(--ink)}
.stMarkdown p.resolved,.stMarkdown p.muted{font:400 13px/19px var(--sans)!important;color:var(--ink2)!important;margin:8px 0 0!important}

/* fields: one quiet trip bar */
[data-testid="stWidgetLabel"] p{font:600 10.5px/16px var(--mono)!important;letter-spacing:.08em;text-transform:uppercase;color:var(--ink3)!important}
[data-baseweb="input"],[data-baseweb="select"]>div,[data-baseweb="textarea"],[data-testid="stDateInputField"],
[data-testid="stNumberInputContainer"],[data-testid="stTimeInputTimeDisplay"]{background:var(--surface)!important;
border:1px solid var(--line-2)!important;border-radius:12px!important}
[data-baseweb="input"]:focus-within,[data-baseweb="select"]>div:focus-within,[data-baseweb="textarea"]:focus-within{
border-color:var(--peacock)!important;box-shadow:0 0 0 3px rgba(15,92,99,.12)!important}
[data-baseweb="input"] input,[data-baseweb="textarea"] textarea{background:transparent!important;color:var(--ink)!important}
[data-testid="stTextInputRootElement"]{min-height:54px;border-radius:999px!important;padding-left:8px;
box-shadow:0 12px 28px -16px rgba(31,42,46,.35)}
[data-testid="stTextInputRootElement"] input{font:500 16px var(--sans)!important}

/* chips */
[data-testid="stButtonGroup"] button{height:36px;min-height:36px;border-radius:999px!important;padding:0 16px!important;
background:var(--surface)!important;border:1px solid var(--line-2)!important;color:var(--ink)!important;
font:500 13.5px var(--sans)!important;transition:border-color .15s var(--ease),background .15s var(--ease)}
[data-testid="stButtonGroup"] button:hover{border-color:var(--ink)!important}
[data-testid="stButtonGroup"] button[aria-pressed="true"],[data-testid="stButtonGroup"] button[aria-checked="true"]{
background:var(--peacock)!important;border-color:var(--peacock)!important;color:#fff!important}
[data-testid="stButtonGroup"] button[aria-pressed="true"] p,[data-testid="stButtonGroup"] button[aria-checked="true"] p{color:#fff!important}
[data-testid="stButtonGroup"] [data-testid="stTooltipIcon"] button,[data-testid="stTooltipIcon"] *{height:auto!important;
min-height:0!important;padding:0!important;border:0!important;background:transparent!important;box-shadow:none!important;color:var(--ink3)!important}

/* buttons */
[data-testid="stBaseButton-primaryFormSubmit"]{height:50px;border-radius:999px!important;color:#fff!important;border:0!important;
background:linear-gradient(180deg,var(--peacock-2),var(--peacock))!important;font:600 15px var(--sans)!important;
box-shadow:0 12px 24px -12px rgba(11,63,69,.65),inset 0 1px 0 rgba(255,255,255,.22),inset 0 -3px 0 rgba(0,0,0,.18)!important;
transition:transform .15s var(--ease),box-shadow .15s var(--ease)}
[data-testid="stBaseButton-primaryFormSubmit"]:hover{transform:translateY(-1px);background:linear-gradient(180deg,#158690,var(--peacock-2))!important}
[data-testid="stBaseButton-primaryFormSubmit"]:active{transform:translateY(1px)}
[data-testid="stBaseButton-secondaryFormSubmit"]{height:44px;border-radius:999px!important;background:var(--surface)!important;
color:var(--ink)!important;border:1px solid var(--ink)!important;font:600 14px var(--sans)!important}

/* panels */
[data-testid="stForm"]{background:var(--surface);border:1px solid var(--line)!important;border-radius:22px;box-shadow:var(--shadow);padding:22px!important}
[data-testid="stExpander"] details{border:1px solid var(--line)!important;border-radius:14px!important;background:var(--sand)}
[data-testid="stExpander"] summary p{font:500 13.5px var(--sans)!important;color:var(--ink2)!important}
[data-testid="stSliderThumbValue"]{font:600 12px var(--mono)!important;color:var(--peacock)!important}
[data-testid="stSliderTickBar"]{font-family:var(--mono)!important;color:var(--ink3)!important}

/* tabs: underline style, like the leading booking sites */
[data-testid="stTabs"] [role="tablist"]{gap:24px;border-bottom:1px solid var(--line)!important;margin-bottom:18px}
[data-testid="stTabs"] [role="tablist"]::after,[data-testid="stTabs"] [role="tablist"]::before{display:none!important}
[data-testid="stTab"]{height:42px;padding:0!important;color:var(--ink2)!important;background:transparent!important;border-radius:0!important}
[data-testid="stTab"] p{font:600 14px var(--sans)!important}
[data-testid="stTab"]:hover{color:var(--ink)!important}
[data-testid="stTab"][aria-selected="true"]{color:var(--ink)!important}
[data-testid="stTab"] .react-aria-SelectionIndicator{background:var(--ink)!important;height:2px!important}

/* results header + conditions */
.results-head h2{font:600 30px/34px var(--serif)!important;letter-spacing:-.02em;color:var(--ink);margin:0 0 6px!important;padding:0!important}
.results-head .sub{font:400 14px/21px var(--sans);color:var(--ink2)}
.results-head .how{font:500 11px/17px var(--mono);letter-spacing:.04em;color:var(--ink3);margin-top:6px}
.strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));background:var(--surface);border:1px solid var(--line);
border-radius:18px;box-shadow:var(--shadow);margin:18px 0 22px;overflow:hidden}
.strip>div{padding:14px 16px;border-left:1px solid var(--line)}.strip>div:first-child{border-left:0}
.strip .k{font:600 10px/15px var(--mono);letter-spacing:.09em;text-transform:uppercase;color:var(--ink3)}
.strip .v{font:600 18px/24px var(--sans);letter-spacing:-.01em;color:var(--ink);margin-top:3px}
.strip .s{font:400 12.5px/18px var(--sans);color:var(--ink2)}
.strip .dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:1px}

.notice{background:var(--surface);border:1px solid var(--line);border-left:4px solid var(--peacock);border-radius:14px;
padding:11px 14px;margin:0 0 12px;font:400 13.5px/20px var(--sans);color:var(--ink)}
.notice.warning{border-left-color:var(--marigold);background:var(--marigold-tint)}.notice.danger{border-left-color:var(--danger)}

/* cards */
.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px;perspective:1200px}
.card{position:relative;background:var(--surface);border:1px solid var(--line);border-radius:22px;overflow:hidden;
box-shadow:var(--shadow);transform-style:preserve-3d;transition:transform .35s var(--ease),box-shadow .35s var(--ease);will-change:transform}
.card:hover{box-shadow:var(--shadow-hi)}
.card:after{content:"";position:absolute;inset:0;pointer-events:none;opacity:0;transition:opacity .3s var(--ease);
background:radial-gradient(380px circle at var(--gx,50%) var(--gy,50%),rgba(255,255,255,.35),transparent 45%)}
.card:hover:after{opacity:1}
.art{position:relative;aspect-ratio:4/3;background-size:cover;background-position:center;overflow:hidden;transform:translateZ(16px)}
.art .poster{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:flex-end;padding:18px}
.art .poster .initial{position:absolute;right:-6px;top:-30px;font:700 180px/1 var(--serif);color:rgba(255,255,255,.16);letter-spacing:-.04em}
.art .poster .kind{font:600 11px/16px var(--mono);letter-spacing:.14em;text-transform:uppercase;color:rgba(255,255,255,.92)}
.art .num{position:absolute;left:14px;top:14px;width:30px;height:30px;border-radius:50%;display:flex;align-items:center;justify-content:center;
font:700 13px/1 var(--mono);color:#fff;border:2px solid #fff;box-shadow:0 4px 10px rgba(0,0,0,.25)}
.art .badge{position:absolute;right:14px;top:14px;background:#fff;color:var(--ink);border-radius:999px;padding:5px 11px;
font:600 12px/16px var(--sans);box-shadow:0 4px 12px -4px rgba(0,0,0,.3)}
.art .credit{position:absolute;right:10px;bottom:8px;font:500 9.5px/12px var(--mono);color:rgba(255,255,255,.75)}
.body{padding:16px 18px 18px;transform:translateZ(24px)}
.card h3{font:600 20px/25px var(--serif)!important;letter-spacing:-.01em;color:var(--ink);margin:0!important;padding:0!important}
.meta{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin:6px 0 10px;font:400 13px/19px var(--sans);color:var(--ink2)}
.meta .star{font-weight:700;color:var(--ink)}
.meta .sep{color:var(--ink3)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px}
.chip{display:inline-flex;align-items:center;gap:6px;height:26px;padding:0 10px;border-radius:999px;background:var(--sand);
font:500 12px var(--sans);color:var(--ink);white-space:nowrap}
.chip i{width:7px;height:7px;border-radius:50%;background:var(--neutral)}
.chip.success i{background:var(--success)}.chip.warning i{background:var(--warning)}.chip.danger i{background:var(--danger)}
.stMarkdown .card p.why{font:400 14px/21px var(--sans)!important;color:var(--ink)!important;margin:0 0 12px!important}
.card details{border-top:1px solid var(--line);margin:4px 0 12px;padding-top:10px}
.card summary{cursor:pointer;list-style:none;font:600 13px var(--sans);color:var(--peacock)}
.card summary::-webkit-details-marker{display:none}
.card summary:after{content:" +"}.card details[open] summary:after{content:" −"}
.card details .inner{padding-top:10px;font:400 13px/19px var(--sans);color:var(--ink2)}
.say{background:var(--peacock-tint);border-radius:14px;padding:12px 14px;margin:0 0 10px}
.say .t{font:600 10px/15px var(--mono);letter-spacing:.09em;text-transform:uppercase;color:var(--peacock);margin-bottom:5px}
.say .l{display:flex;gap:8px;font:400 13px/20px var(--sans);color:var(--ink)}
.say .l b{font:700 13px/20px var(--mono);width:10px}.say .l b.p{color:var(--success)}.say .l b.m{color:var(--danger)}
.say .src{font:500 10px/15px var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--ink2);margin-top:5px}
.actions{display:flex;flex-wrap:wrap;align-items:center;gap:14px}
.btn{display:inline-flex;align-items:center;height:36px;padding:0 16px;border-radius:999px;background:var(--ink);color:#fff!important;
text-decoration:none!important;font:600 13px var(--sans);transition:background .15s var(--ease)}
.btn:hover{background:var(--peacock)}
.link{font:600 13px var(--sans);color:var(--ink)!important;text-decoration:underline!important;text-underline-offset:4px;
text-decoration-color:var(--line-2)!important}

/* featured top pick */
.card.feature{display:grid;grid-template-columns:1.15fr 1fr;margin:0 0 20px}
.card.feature .art{aspect-ratio:auto;min-height:340px}
.card.feature h3{font-size:28px!important;line-height:32px!important}
.card.feature .body{padding:24px 26px;align-self:center}
.eyebrow{font:600 10.5px/16px var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--terracotta);margin-bottom:8px}

/* day plan: itinerary with travel between stops */
.plan-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:10px;margin:0 0 14px}
.plan-head b{font:600 22px/28px var(--serif);color:var(--ink)}
.plan-head span{font:400 13.5px/20px var(--sans);color:var(--ink2)}
.leg{display:flex;align-items:center;gap:10px;margin:0 0 0 15px;padding:8px 0 8px 24px;border-left:2px dashed var(--line-2);
font:500 12.5px/18px var(--sans);color:var(--ink2)}
.leg em{font-style:normal;font-family:var(--mono);font-size:11px;color:var(--ink3)}
.stop{display:grid;grid-template-columns:32px 1fr 92px;gap:14px;align-items:center;background:var(--surface);border:1px solid var(--line);
border-radius:18px;padding:12px 12px 12px 12px;box-shadow:var(--shadow)}
.stop .n{width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:#fff;font:700 13px/1 var(--mono)}
.stop .time{font:600 11px/15px var(--mono);letter-spacing:.06em;color:var(--peacock)}
.stop .name{font:600 16px/21px var(--serif);color:var(--ink)}
.stop .detail{font:400 12.5px/18px var(--sans);color:var(--ink2)}
.stop .thumb{height:64px;border-radius:12px;background-size:cover;background-position:center}
.stop.start{grid-template-columns:32px 1fr;box-shadow:none;background:var(--sand-2)}
.legend{display:flex;flex-wrap:wrap;gap:12px;margin:12px 0 0}
.legend span{display:inline-flex;align-items:center;gap:6px;font:500 12.5px var(--sans);color:var(--ink2)}
.legend i{width:10px;height:10px;border-radius:3px;display:inline-block}
.foot{margin-top:52px;padding-top:16px;border-top:1px solid var(--line);font:500 11px/17px var(--mono);color:var(--ink3)}
@media (max-width:720px){.grid{grid-template-columns:1fr}.card.feature{grid-template-columns:1fr}.card.feature .art{min-height:220px}}
@media (max-width:640px){.block-container{padding:12px 14px 56px!important}.results-head h2{font-size:24px!important;line-height:29px!important}
.stop{grid-template-columns:32px 1fr}.stop .thumb{display:none}.strip{grid-template-columns:1fr 1fr}
.strip>div:nth-child(3){border-left:0}.strip>div:nth-child(n+3){border-top:1px solid var(--line)}}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
</style>
"""

# 3D tilt for place cards: follows the pointer with a moving highlight. Off for touch and reduced motion.
TILT_JS = """
<script>
(function(){
  const doc = window.parent.document, win = window.parent;
  if (win.matchMedia('(prefers-reduced-motion: reduce)').matches || !win.matchMedia('(hover: hover)').matches) return;
  function bind(){
    doc.querySelectorAll('.card:not([data-tilt])').forEach(function(c){
      c.dataset.tilt = '1';
      const max = c.classList.contains('feature') ? 3 : 6;
      c.addEventListener('mousemove', function(e){
        const r = c.getBoundingClientRect(), x = (e.clientX - r.left) / r.width - .5, y = (e.clientY - r.top) / r.height - .5;
        c.style.transform = 'rotateY(' + (x * max).toFixed(2) + 'deg) rotateX(' + (-y * max).toFixed(2) + 'deg) translateY(-3px)';
        c.style.setProperty('--gx', ((x + .5) * 100).toFixed(1) + '%');
        c.style.setProperty('--gy', ((y + .5) * 100).toFixed(1) + '%');
      });
      c.addEventListener('mouseleave', function(){ c.style.transform = ''; });
    });
  }
  bind();
  if (!win.__radiusTilt) { win.__radiusTilt = new MutationObserver(bind); win.__radiusTilt.observe(doc.body, {childList: true, subtree: true}); }
})();
</script>
"""


def hero_html(pin: tuple[float, float] | None, place: str | None) -> str:
    """Rotating 3D globe (three.js r128): marigold city pins, coral pin for the chosen place, peacock night."""
    cities = [[lat, lon] for lat, lon in CITY_COORDS.values()]
    data = json.dumps({"cities": cities, "pin": list(pin) if pin else None})
    sub = (f"Planning a day around {esc(place)}." if place
           else "Tell us what you enjoy. We check the weather for your time, live traffic and what visitors say.")
    return """
<!doctype html><html><head><meta charset="utf-8"><style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&family=Hanken+Grotesk:wght@400;500&family=IBM+Plex+Mono:wght@600&display=swap');
html,body{margin:0;height:100%;overflow:hidden;font-family:"Hanken Grotesk",system-ui,sans-serif}
.wrap{position:relative;height:100%;border-radius:22px;overflow:hidden;
background:radial-gradient(620px 380px at 80% 45%,rgba(242,163,58,.22) 0%,transparent 60%),
radial-gradient(520px 340px at 0% 120%,rgba(210,96,58,.28) 0%,transparent 60%),linear-gradient(160deg,#071F24 0%,#0B3F45 100%)}
canvas{position:absolute;inset:0;width:100%;height:100%;display:block}
.copy{position:absolute;left:34px;top:30px;right:46%;color:#fff;z-index:2}
.brand{display:flex;align-items:center;gap:8px;font:600 13px "IBM Plex Mono",monospace;letter-spacing:.04em;color:#F7E7C8}
.brand span{font-size:10px;letter-spacing:.12em;padding:2px 8px;border-radius:999px;border:1px solid rgba(247,231,200,.4)}
h1{font-family:Fraunces,Georgia,serif;font-weight:600;font-size:48px;line-height:1.02;letter-spacing:-.025em;margin:54px 0 14px;color:#FBF7F1}
h1 em{font-style:italic;color:#F2A33A}
p{margin:0;color:#B9D3D2;font-size:15px;line-height:1.55;max-width:350px}
@media (max-width:640px){.copy{right:22px;left:22px;top:22px}h1{font-size:34px;margin-top:26px}}
</style></head><body><div class="wrap"><canvas id="c"></canvas>
<div class="copy"><div class="brand">radius <span>INDIA</span></div><h1>A day that fits,<br><em>wherever you are.</em></h1><p>__SUB__</p></div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script>
(function(){
  const D = __DATA__;
  if (!window.THREE) return;
  const canvas = document.getElementById('c');
  let renderer; try { renderer = new THREE.WebGLRenderer({canvas, antialias: true, alpha: true}); } catch (e) { return; }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(38, 1, .1, 100);
  camera.position.set(0, 0, 6.2);
  const globe = new THREE.Group(); scene.add(globe);
  const toVec = (lat, lon, r) => { const p = (90 - lat) * Math.PI / 180, t = (lon + 180) * Math.PI / 180;
    return new THREE.Vector3(-r * Math.sin(p) * Math.cos(t), r * Math.cos(p), r * Math.sin(p) * Math.sin(t)); };
  globe.add(new THREE.Mesh(new THREE.SphereGeometry(.995, 48, 48), new THREE.MeshBasicMaterial({color: 0x082B30})));
  const glow = new THREE.Mesh(new THREE.SphereGeometry(1.12, 48, 48), new THREE.MeshBasicMaterial({color: 0xF2A33A, transparent: true, opacity: .07, side: THREE.BackSide}));
  scene.add(glow);
  const N = 2600, pos = [], col = [], c1 = new THREE.Color(0x3FB6AE), c2 = new THREE.Color(0xF2C46B);
  for (let i = 0; i < N; i++) { const y = 1 - (i / (N - 1)) * 2, r = Math.sqrt(1 - y * y), th = i * 2.399963;
    pos.push(Math.cos(th) * r, y, Math.sin(th) * r); const c = c1.clone().lerp(c2, (y + 1) / 2); col.push(c.r, c.g, c.b); }
  const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
  globe.add(new THREE.Points(g, new THREE.PointsMaterial({size: .014, vertexColors: true, transparent: true, opacity: .8})));
  D.cities.forEach(([la, lo]) => { const m = new THREE.Mesh(new THREE.SphereGeometry(.018, 12, 12), new THREE.MeshBasicMaterial({color: 0xF2A33A}));
    m.position.copy(toVec(la, lo, 1.01)); globe.add(m); });
  let pulse = null;
  if (D.pin) { const v = toVec(D.pin[0], D.pin[1], 1.01);
    const dot = new THREE.Mesh(new THREE.SphereGeometry(.032, 16, 16), new THREE.MeshBasicMaterial({color: 0xFF7A59})); dot.position.copy(v); globe.add(dot);
    pulse = new THREE.Mesh(new THREE.RingGeometry(.04, .055, 32), new THREE.MeshBasicMaterial({color: 0xFF7A59, transparent: true, side: THREE.DoubleSide}));
    pulse.position.copy(v); pulse.lookAt(v.clone().multiplyScalar(2)); globe.add(pulse);
    const beam = new THREE.Mesh(new THREE.CylinderGeometry(.004, .004, .35, 8), new THREE.MeshBasicMaterial({color: 0xFFB59F, transparent: true, opacity: .85}));
    beam.position.copy(toVec(D.pin[0], D.pin[1], 1.18)); beam.lookAt(new THREE.Vector3(0, 0, 0)); beam.rotateX(Math.PI / 2); globe.add(beam); }
  const ring = new THREE.Mesh(new THREE.TorusGeometry(1.42, .004, 8, 160), new THREE.MeshBasicMaterial({color: 0xF2C46B, transparent: true, opacity: .35}));
  ring.rotation.x = 1.25; scene.add(ring);
  // toVec puts lon 80E at -z; a half turn brings India to face the camera (+z), the x tilt centres 22N.
  globe.rotation.y = Math.PI; globe.rotation.x = .38;
  const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let my = 0; window.addEventListener('mousemove', e => { my = e.clientY / innerHeight - .5; });
  let lw = 0, lh = 0;
  function size(){ const w = window.innerWidth, h = window.innerHeight; if (!w || !h || (w === lw && h === lh)) return;
    lw = w; lh = h; const wide = w > 640; renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
    const x = wide ? 1.65 : 0, y = wide ? -.05 : -1.6; globe.position.set(x, y, 0); glow.position.set(x, y, 0); ring.position.set(x, y, 0); }
  const base = globe.rotation.y; let t = 0;
  (function frame(){ size(); t += .016;
    if (!still) { globe.rotation.y = base + Math.sin(t * .18) * .45; ring.rotation.z += .0015; }
    globe.rotation.x = .38 + my * .12;
    if (pulse) { const s = 1 + (t * 1.4 % 1) * 2.2; pulse.scale.set(s, s, s); pulse.material.opacity = 1 - (t * 1.4 % 1); }
    renderer.render(scene, camera); requestAnimationFrame(frame); })();
})();
</script></body></html>""".replace("__DATA__", data).replace("__SUB__", sub)


def esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def html_block(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def notice(text: str, kind: str = "info") -> None:
    html_block(f'<div class="notice {kind}">{text}</div>')


def step(number: str, title: str) -> None:
    html_block(f'<div class="step"><b>{esc(number)}</b><span>{esc(title)}</span></div>')


def hex_rgb(color: str) -> list[int]:
    color = color.lstrip("#")
    return [int(color[i:i + 2], 16) for i in (0, 2, 4)]


def color_of(category: str) -> str:
    return CATEGORY_COLOR.get(category, "#0F5C63")


def name_of(category: str) -> str:
    return CATEGORY_NAME.get(category, category.replace("_", " ").title())


# =============================================================================== inputs
def where(demo: bool) -> tuple[float, float, str] | None:
    step("01", "Where are you starting?")
    if demo:
        html_block('<p class="resolved">Bhubaneswar, Odisha · sample data</p>')
        return DEMO_CENTER[0], DEMO_CENTER[1], "Bhubaneswar"
    typed = st.text_input("Starting point", placeholder="Search a town, city or landmark in India",
                          label_visibility="collapsed")
    pick = None if typed.strip() else st.pills("Popular", POPULAR, label_visibility="collapsed")
    query = typed.strip() or (pick or "")
    if not query:
        return None
    if st.session_state.get("last_query") != query:
        try:
            st.session_state.place_options = run(geocode(query, SETTINGS))
        except Exception:
            notice("Place search isn’t responding. Try again in a moment.", "warning")
            st.session_state.place_options = []
        st.session_state.last_query = query
    options = st.session_state.get("place_options") or []
    if not options:
        notice(f"No place in India matches “{esc(query)}”. Check the spelling or try a nearby town.", "warning")
        return None
    if len(options) > 1:
        p = options[st.selectbox("Which one?", range(len(options)), format_func=lambda i: options[i].label)]
    else:
        p = options[0]
        html_block(f'<p class="resolved">{esc(p.label)}</p>')
    return p.latitude, p.longitude, p.label.split(",")[0]


def preferences(lat: float, lon: float) -> PreferenceProfile | None:
    step("02", "Shape your day")
    with st.form("prefs", border=False):
        now = datetime.now(IST)
        c1, c2, c3, c4 = st.columns([1.15, .9, 1, 1])
        day = c1.date_input("When", value=now.date(), min_value=now.date(), max_value=now.date() + timedelta(days=15),
                            format="DD/MM/YYYY")
        default_start = time(max(6, min(now.hour + 1, 20)), 0) if day == now.date() else time(9, 0)
        start = c2.time_input("From", value=default_start, step=timedelta(minutes=30))
        hours = c3.slider("Hours", 1.0, 12.0, 4.0, 0.5)
        radius = c4.slider("Within (km)", 2, 25, 10)
        interests = st.pills("You enjoy", list(INTERESTS), selection_mode="multi",
                             default=["historical", "religious"], format_func=INTERESTS.get)
        who = st.pills("Travelling", list(TRAVELLERS), selection_mode="multi", format_func=TRAVELLERS.get,
                       help=TRAVELLER_HELP)
        with st.expander("More preferences"):
            text = st.text_area("Anything else", max_chars=1000, height=80,
                                placeholder="Quiet places, nothing too crowded, something for kids. English or Hinglish.")
            fee_cap = st.number_input("Max ticket per person (₹, 0 = any)", 0, 100_000, 0, 50)
        submitted = st.form_submit_button("Show my day", type="primary", use_container_width=True)
    if not submitted:
        return None
    if not interests and not text.strip():
        notice("Pick at least one thing you enjoy.", "warning")
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
TRAFFIC = {"light": ("success", "light traffic"), "moderate": ("warning", "moderate traffic"),
           "heavy": ("danger", "heavy traffic")}
WEATHER_FIT = {"suitable": ("success", "Good weather for it"), "acceptable": ("warning", "Weather so-so"),
               "unsuitable": ("danger", "Poor weather for it")}
OPENING = {"open": "success", "opens_later": "warning", "closed": "danger"}
BAD_SKY = ("rain", "drizzle", "thunder", "shower", "storm")


def chip(text: str, kind: str = "") -> str:
    return f'<span class="chip {kind}"><i></i>{esc(text)}</span>'


def badges(result: RecommendationResult) -> dict[str, str]:
    """At most one honest badge per place, each derived from the data (Tripadvisor-style, but explainable)."""
    items = result.recommendations
    out: dict[str, str] = {}
    if items:
        out[items[0].poi_id] = "Top pick"
    rated = [i for i in items if i.facts.rating is not None and i.facts.rating_count >= 50]
    if rated:
        best = max(rated, key=lambda i: (i.facts.rating, i.facts.rating_count))
        out.setdefault(best.poi_id, "Best rated")
    timed = [i for i in items if i.travel]
    if len(timed) > 1:
        out.setdefault(min(timed, key=lambda i: i.travel.minutes).poi_id, "Closest")
    sky = (result.weather.condition or "").lower() if result.weather else ""
    if any(word in sky for word in BAD_SKY):
        for i in items:
            if i.category in INDOOR and i.weather_suitability == "suitable":
                out.setdefault(i.poi_id, "Good in this weather")
    return out


def visit_minutes(result: RecommendationResult) -> dict[str, int]:
    planned = {s.poi_id: s.visit_minutes for s in (result.itinerary.stops if result.itinerary else [])}
    return {i.poi_id: planned.get(i.poi_id) or DEFAULT_VISIT_MINUTES.get(i.category, 60) for i in result.recommendations}


def results_head(result: RecommendationResult) -> None:
    n = len(result.recommendations)
    place = st.session_state.get("place_label") or "you"
    req = result.request
    when = req.client_datetime
    bits = [f"{when:%a %d %b}, from {when:%H:%M}" if when else "", f"{req.available_hours:g} h",
            f"within {req.max_distance_km:g} km", " · ".join(INTERESTS.get(i, i) for i in req.interests)]
    html_block(f'<div class="results-head"><h2>{n} {"pick" if n == 1 else "picks"} around {esc(place)}</h2>'
               f'<div class="sub">{esc(" · ".join(b for b in bits if b))}</div>'
               '<div class="how">RANKED BY WHAT YOU ENJOY, RATINGS, DISTANCE, OPENING HOURS, THE FORECAST FOR YOUR '
               'TIME AND TRAFFIC</div></div>')


def strip(result: RecommendationResult) -> None:
    cells = []
    w = result.weather
    if w and w.temperature_c is not None:
        rain = (f"{w.max_precipitation_probability:.0f}% chance of rain"
                if w.max_precipitation_probability is not None else "")
        cells.append(("Weather · your time", f"{w.temperature_c:.0f}°C {w.condition or ''}".strip(), rain, ""))
    aq = result.air_quality
    if aq and aq.us_aqi is not None:
        dot = "#1E8A5A" if aq.us_aqi <= 50 else "#C77A12" if aq.us_aqi <= 150 else "#C63D2F"
        cells.append(("Air quality", f"AQI {aq.us_aqi:.0f}",
                      (aq.category or "").replace("_", " ").replace("sensitive", "for sensitive groups").capitalize(),
                      dot))
    traffic = [i.travel.traffic for i in result.recommendations if i.travel and i.travel.method == "google_traffic"]
    if traffic:
        worst = "heavy" if "heavy" in traffic else "moderate" if "moderate" in traffic else "light"
        cells.append(("Traffic · your start", TRAFFIC[worst][1].capitalize(), "Live from Google Maps",
                      {"light": "#1E8A5A", "moderate": "#C77A12", "heavy": "#C63D2F"}[worst]))
    if result.season:
        cells.append(("Season", result.season, "", ""))
    if cells:
        html_block('<div class="strip">' + "".join(
            f'<div><div class="k">{esc(k)}</div><div class="v">'
            + (f'<span class="dot" style="background:{d}"></span>' if d else "")
            + f'{esc(v)}</div>' + (f'<div class="s">{esc(s)}</div>' if s else "") + "</div>"
            for k, v, s, d in cells) + "</div>")


def art(item: RecommendationItem, badge: str | None) -> str:
    color = color_of(item.category)
    kind = name_of(item.category)
    if item.image_url:
        inner = (f'<div class="art" style="background-image:linear-gradient(180deg,rgba(0,0,0,.05) 50%,rgba(0,0,0,.45)),'
                 f'url(\'{esc(item.image_url)}\')"><div class="poster"><div class="kind">{esc(kind)}</div></div>'
                 + ('<span class="credit">Wikimedia Commons</span>' if item.image_attribution else ""))
    else:
        initial = esc((item.name.strip() or "·")[0].upper())
        inner = (f'<div class="art" style="background:linear-gradient(150deg,{color} 0%,{color}d9 45%,#0B3F45 135%)">'
                 f'<div class="poster"><div class="initial">{initial}</div><div class="kind">{esc(kind)}</div></div>')
    inner += f'<span class="num" style="background:{color}">{item.rank}</span>'
    if badge:
        inner += f'<span class="badge">{esc(badge)}</span>'
    return inner + "</div>"


def card(item: RecommendationItem, badge: str | None, minutes: int, feature: bool = False) -> str:
    f = item.facts
    meta = []
    if f.rating is not None:
        meta.append(f'<span class="star">★ {f.rating:.1f}</span><span>({f.rating_count:,})</span>')
    meta.append(f"<span>{esc(name_of(item.category))}</span>")
    if item.travel:
        traffic = TRAFFIC.get(item.travel.traffic, ("", ""))[1] if item.travel.method == "google_traffic" else ""
        est = "" if item.travel.method == "google_traffic" else " (est.)"
        meta.append(f"<span>{item.travel.minutes} min drive{est}{(' · ' + traffic) if traffic else ''}</span>")
    else:
        meta.append(f"<span>{f.distance_km:.1f} km</span>")
    meta_html = '<span class="sep">·</span>'.join(meta)

    chips = []
    if item.opening and item.opening.status in OPENING:
        chips.append(chip(item.opening.label, OPENING[item.opening.status]))
    if item.weather_suitability in WEATHER_FIT:
        kind, label = WEATHER_FIT[item.weather_suitability]
        chips.append(chip(label, kind))
    chips.append(chip(f"Plan about {minutes} min"))

    critical = "".join(f'<div class="notice danger">{esc(n.text)}</div>'
                       for n in item.notices if n.severity == "critical")
    more = []
    rs = item.review_summary
    say_html = ""
    if rs:
        lines = []
        if rs.likes:
            lines += [f'<div class="l"><b class="p">+</b><span>{esc(x)}</span></div>' for x in rs.likes]
        if rs.dislikes:
            lines += [f'<div class="l"><b class="m">−</b><span>{esc(x)}</span></div>' for x in rs.dislikes]
        if not lines and rs.text:
            lines = [f'<div class="l"><span>{esc(rs.text)}</span></div>']
        if lines:
            source = "Google reviews" if "google_places" in f.rating_sources else "reviews"
            how = " · AI summary" if rs.method == "ai" else ""
            say_html = (f'<div class="say"><div class="t">What visitors say</div>{"".join(lines)}'
                        f'<div class="src">From {rs.based_on} {source}{how}</div></div>')
    elif item.review_insights:
        insights = "".join(f'<div class="l"><span>{esc(x)}</span></div>' for x in item.review_insights[:2])
        say_html = f'<div class="say"><div class="t">Visitor Highlights</div>{insights}</div>'

    if item.opening and item.opening.hours_today:
        more.append(f"<div>Hours today: {esc(item.opening.hours_today)}</div>")
    fee = next((n.text for n in item.notices if n.kind == "fee"), None)
    if fee:
        more.append(f"<div>{esc(fee)}</div>")
    more += [f"<div>{esc(n.text)}</div>" for n in item.notices if n.severity == "warning" and n.kind != "accessibility"]
    if item.season_note:
        more.append(f"<div>{esc(item.season_note)}</div>")
    details = ""
    if more:
        is_open = " open" if feature else ""
        details = (f'<details{is_open}><summary>More about this place</summary><div class="inner">{"".join(more)}'
                   "</div></details>")

    actions = []
    if item.maps_url:
        actions.append(f'<a class="btn" href="{esc(item.maps_url)}" target="_blank" rel="noopener">Directions</a>')
    if item.wiki_url:
        actions.append(f'<a class="link" href="{esc(item.wiki_url)}" target="_blank" rel="noopener">Read about it</a>')
    if item.website:
        actions.append(f'<a class="link" href="{esc(item.website)}" target="_blank" rel="noopener">Website</a>')

    eyebrow = '<div class="eyebrow">Start here</div>' if feature else ""
    return (f'<article class="card{" feature" if feature else ""}">{art(item, badge)}<div class="body">{eyebrow}'
            f'<h3>{esc(item.name)}</h3><div class="meta">{meta_html}</div><div class="chips">{"".join(chips)}</div>'
            f'<p class="why">{esc(item.reason)}</p>{say_html}{critical}{details}<div class="actions">{"".join(actions)}</div>'
            "</div></article>")


def places(result: RecommendationResult) -> None:
    marks, minutes = badges(result), visit_minutes(result)
    items = result.recommendations
    first, rest = items[0], items[1:]
    html_block(card(first, marks.get(first.poi_id), minutes[first.poi_id], feature=True)
               + (f'<div class="grid">{"".join(card(i, marks.get(i.poi_id), minutes[i.poi_id]) for i in rest)}</div>'
                  if rest else ""))
    components.html(TILT_JS, height=0)


def day_plan(result: RecommendationResult) -> None:
    it = result.itinerary
    if not it or not it.stops:
        html_block('<p class="muted">No timed plan fits the hours you have. Try more time or a smaller distance.</p>')
        return
    basis = "with live traffic" if it.travel_method == "google_traffic" else "drive times estimated"
    by_id = {i.poi_id: i for i in result.recommendations}
    html_block(f'<div class="plan-head"><b>{it.start:%H:%M} – {it.end:%H:%M}</b>'
               f'<span>{len(it.stops)} stops · about {it.total_travel_minutes} min on the road, {basis}</span></div>')
    parts = [f'<div class="stop start"><div class="n" style="background:#0F5C63">●</div><div>'
             f'<div class="time">{it.start:%H:%M}</div><div class="name">Start</div>'
             f'<div class="detail">{esc(st.session_state.get("place_label") or "Your starting point")}</div></div></div>']
    for s in it.stops:
        item = by_id.get(s.poi_id)
        color = color_of(item.category) if item else "#0F5C63"
        wait = f" · then waits {s.wait_minutes} min for opening" if s.wait_minutes else ""
        parts.append(f'<div class="leg">{s.travel_minutes} min drive<em>{"" if it.travel_method == "google_traffic" else "EST."}</em></div>')
        thumb = (f'<div class="thumb" style="background-image:url(\'{esc(item.image_url)}\')"></div>' if item and item.image_url
                 else f'<div class="thumb" style="background:linear-gradient(150deg,{color},#0B3F45)"></div>')
        parts.append(f'<div class="stop"><div class="n" style="background:{color}">{item.rank if item else "·"}</div><div>'
                     f'<div class="time">{s.arrive:%H:%M} – {s.depart:%H:%M}</div><div class="name">{esc(s.name)}</div>'
                     f'<div class="detail">{s.visit_minutes} min here{wait}</div></div>{thumb}</div>')
    html_block("".join(parts))
    if it.skipped:
        html_block(f'<p class="muted">Didn’t fit in your time: {esc(", ".join(it.skipped))}</p>')


def _rating_prefix(item: RecommendationItem) -> str:
    return f"★ {item.facts.rating:.1f} · " if item.facts.rating is not None else ""


def map_3d(result: RecommendationResult) -> None:
    """Tilted 3D map: numbered pillars (height = rating, colour = category) and the day plan as arcs."""
    items = result.recommendations
    start = [result.request.longitude, result.request.latitude]
    pillars = [{"position": [i.longitude, i.latitude], "name": f"{i.rank} · {i.name}",
                "detail": f"{_rating_prefix(i)}{name_of(i.category)}",
                "height": 180 + ((i.facts.rating or 3.5) - 3) * 420 + max(0, 8 - i.rank) * 45,
                "color": [*hex_rgb(color_of(i.category)), 240], "label": str(i.rank)} for i in items]
    for p in pillars:
        p["top"] = [*p["position"], p["height"] + 70]
    by_id = {i.poi_id: i for i in items}
    route = [start, *[[by_id[s.poi_id].longitude, by_id[s.poi_id].latitude]
                      for s in (result.itinerary.stops if result.itinerary else []) if s.poi_id in by_id]]
    arcs = [{"from": a, "to": b} for a, b in itertools.pairwise(route)]
    lats = [p["position"][1] for p in pillars] + [start[1]]
    lons = [p["position"][0] for p in pillars] + [start[0]]
    lat0 = sum(lats) / len(lats)
    span = max(max(lats) - min(lats), (max(lons) - min(lons)) * math.cos(math.radians(lat0)), 0.004)
    zoom = max(11.0, min(15.5, math.log2(360 / span) - .45))  # fit all picks and the start point
    radius = max(35.0, min(200.0, span * 111_000 * .028))  # ~3% of the area's width
    view = pdk.ViewState(latitude=lat0 - span * .18, longitude=sum(lons) / len(lons), zoom=zoom, pitch=58, bearing=-28)
    layers = [
        pdk.Layer("ScatterplotLayer", data=[{**p, "glow": [*p["color"][:3], 70]} for p in pillars],
                  get_position="position", get_radius=radius * 2.6, get_fill_color="glow"),
        pdk.Layer("ColumnLayer", data=pillars, get_position="position", get_elevation="height", elevation_scale=1,
                  radius=radius, get_fill_color="color", pickable=True, auto_highlight=True, extruded=True,
                  material={"ambient": .6, "diffuse": .6, "shininess": 40}),
        pdk.Layer("TextLayer", data=pillars, get_position="top", get_text="label", get_size=18,
                  get_color=[255, 255, 255, 255], font_weight=700, outline_width=4, outline_color=[7, 31, 36, 255],
                  font_settings={"sdf": True}, billboard=True),
        pdk.Layer("ArcLayer", data=arcs, get_source_position="from", get_target_position="to",
                  get_source_color=[242, 163, 58, 235], get_target_color=[255, 122, 89, 235], get_width=5,
                  get_height=.6),
        pdk.Layer("ScatterplotLayer", data=[{"position": start}], get_position="position", get_radius=110,
                  get_fill_color=[15, 92, 99, 255], get_line_color=[255, 255, 255, 255], line_width_min_pixels=3,
                  stroked=True),
    ]
    st.pydeck_chart(pdk.Deck(layers=layers, initial_view_state=view, map_provider="carto", map_style="dark",
                             tooltip={"html": "<b>{name}</b><br/>{detail}",
                                      "style": {"backgroundColor": "#0B3F45", "color": "white",
                                                "fontFamily": "Hanken Grotesk, sans-serif", "borderRadius": "10px"}}),
                    height=460)
    seen = list(dict.fromkeys(i.category for i in items))
    legend = "".join(f'<span><i style="background:{color_of(c)}"></i>{esc(name_of(c))}</span>' for c in seen)
    html_block(f'<div class="legend">{legend}<span><i style="background:#0F5C63;border-radius:50%"></i>Start</span>'
               '<span><i style="background:linear-gradient(90deg,#F2A33A,#FF7A59)"></i>Day plan route</span></div>')
    html_block('<p class="muted">Numbers match the list. Taller pillars are better rated. Drag to rotate, scroll to zoom.</p>')


def show_result(result: RecommendationResult) -> None:
    step("03", "Your day")
    if not result.recommendations:
        notice("Nothing fits yet. Try a larger distance, more time or another interest.", "warning")
        return
    results_head(result)
    strip(result)
    for ev in result.events:
        notice(f"<b>{esc(ev.name)}</b>, {ev.start:%d %b}, near {esc(ev.location_name)}. {esc(ev.description)}",
               "warning")
    if result.request.visitor.non_hindu_visitor and result.excluded.get("entry_restricted"):
        notice("Temples where only Hindus may enter are left out.")
    tab_places, tab_plan, tab_map = st.tabs(["Places", "Day plan", "3D map"])
    with tab_places:
        places(result)
    with tab_plan:
        day_plan(result)
    with tab_map:
        map_3d(result)


def refine_box(store, sts: Statuses) -> None:
    with st.form("refine", clear_on_submit=True, border=False):
        c1, c2 = st.columns([4, 1], vertical_alignment="bottom")
        msg = c1.text_input("Not quite right?", placeholder="Fewer temples · only 2 hours · something for kids")
        go = c2.form_submit_button("Update", use_container_width=True)
    if go and msg.strip():
        with st.spinner("Updating your picks"):
            try:
                profile, changes, result = run(refine(store, st.session_state.profile, msg.strip(), KEYS, sts,
                                                      SETTINGS))
            except Exception:
                notice("That change couldn’t be applied. Try saying it differently.", "warning")
                return
        st.session_state.update(profile=profile, changes=changes, result=result)
        st.rerun()
    if st.session_state.get("changes"):
        html_block(f'<p class="muted">Updated: {esc(" · ".join(st.session_state.changes))}</p>')


# =============================================================================== page
def main() -> None:
    html_block(CSS)
    hero_slot = st.empty()  # filled after the place is known, so the globe can pin it
    for k, v in {"result": None, "profile": None, "changes": None, "area_key": None, "place_options": []}.items():
        st.session_state.setdefault(k, v)
    demo = st.query_params.get("demo") == "1"  # developer-only sample data: /?demo=1
    sts = statuses()

    loc = where(demo)
    with hero_slot.container():
        components.html(hero_html((loc[0], loc[1]) if loc else None, loc[2] if loc else None), height=330)
    if loc is None:
        return
    lat, lon, label = loc
    st.session_state.place_label = label
    profile = preferences(lat, lon)
    if profile is not None:
        try:
            with st.spinner("Finding places, reviews and weather"):
                store, _ = get_area(lat, lon, profile.max_distance_km, demo, sts)
            with st.spinner("Checking traffic and building your day"):
                result, _ = run(recommend(store, profile, KEYS, sts, SETTINGS))
        except LiveDataError as exc:
            notice(esc(str(exc)), "warning")
            return
        except Exception as exc:
            notice("Something went wrong while planning your day. Please try again.", "danger")
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
    html_block('<div class="foot">Places © OpenStreetMap contributors · Wikipedia (CC BY-SA) · weather Open-Meteo'
               + (" · ratings, reviews and traffic Google Maps" if KEYS.google else "")
               + " · map © CARTO. Rules, prices and timings change; check locally before you go.</div>")


main()
