# DESIGN.md: Radius

The design system for **Radius**, a day planner for India. It recommends places from what the traveller enjoys, the forecast for their time window, live traffic and real reviews.

Status: **v3.0, implemented** in `streamlit_app.py` (constants `CSS`, `TILT_JS`, `CATEGORY_COLOR`, and functions `hero_html`, `results_head`, `strip`, `card`, `badges`, `day_plan`, `map_3d`) and `.streamlit/config.toml`.

| Version | Date | Direction |
|---|---|---|
| v1.0 | 2026-11-19 | Monochrome notebook (the fanout.sh language from `C:\Projects\listing_web\DESIGN.md`) |
| v2.0 | 2026-11-19 | 3D and bold colour: globe hero, 3D map, tilting glass cards, indigo→pink gradient |
| **v3.0** | 2026-11-20 | **Research-led product pass.** Same 3D features, rebuilt around patterns from leading travel products (section 1). New palette, **Peacock & Marigold**, replaces the indigo→pink gradient. |

---

## 1. Research (2026-11-20)

Five products were studied live in a browser, each for the job it does best. Values marked **[DOM]** were measured from computed styles; everything else is from screenshots.

| Product | Studied for | What it does | Applied in Radius |
|---|---|---|---|
| **Wanderlog** (wanderlog.com) | Itinerary + map | Day itinerary and map in one view. Each stop card has a **number that matches its map pin**, a 2-line blurb and a photo thumbnail on the right. **Between stops: "20 min · 2m · Directions".** The place popover shows today's hours with weekday chips, "**People typically spend 2 hrs here**", tags, rating with source, and "Open in Google Maps / Tripadvisor / Yelp". | Day plan rebuilt as stop cards with drive links between them; numbers on cards, plan and map pillars; "Plan about N min" chip; a Directions action on every card |
| **Airbnb Experiences** | Search and cards | A compact search pill at the top, a row of filter chips, and a count heading ("**Explore 24 experiences nearby**", 20/600 **[DOM]**). **Photo-first cards** (about 1:1, rounded, no border) with one white badge top-left ("Trending"/"New") and a heart top-right. **At most 3 short lines** below: title 14/500 **[DOM]**, "Food tours · 3.5 hours", price and "★ 5.0 · 34 ratings". | A compact one-row trip bar plus chip rows; the "N picks around {place}" heading; artwork-first cards with one badge; card text cut to title, one meta line, chips and one "why" line, with the rest behind "More about this place" |
| **Tripadvisor** (Rajasthan attractions) | Ranking and trust | A **numbered** ranking ("1. Amber Palace") with "4.6 (18,734)" and a plain category. **Merit badges that explain themselves** ("Travellers' Choice: top 10%…"). One quoted visitor line. A **transparency note**: "rankings are informed by reviews, ratings…". | Honest, data-derived badges (Top pick · Best rated · Closest · Good in this weather); a "how we ranked" line under the heading; "What visitors say" kept short |
| **Google Flights** (the Things-to-do link redirected here) | Search composition | A single search card floating over the header art, with labelled sections (Round trip · passengers · From · To · Dates) and one centred action | The trip bar groups When · From · Hours · Within in one row, with a single full-width primary action |
| **Lonely Planet** (India) | Editorial tone | Large display type on a **deep earthy panel** (olive), **mono uppercase metadata** ("ASIA", "AUTHOR \| DATE"), strong photography, restrained colour | A serif display face (Fraunces) for titles and place names; mono metadata kept; the neon gradient replaced by deep, earthy, India-rooted colour |

**The palette decision.** The leading travel brands each use *one* confident brand colour on warm neutrals: Airbnb coral, Tripadvisor green, Lonely Planet olive, Wanderlog coral. The v2 indigo→violet→pink gradient appears in none of them, and it is the most recognisable "generic AI app" look. v3 uses a single brand colour, **peacock teal**, with **marigold** and **saffron** accents on **sand**. These colours have Indian roots (peacock, marigold garlands, saffron, rani pink).

---

## 2. Visual theme

**A travel magazine that knows the weather.**
- Warm sand pages with white cards.
- Deep peacock night for the two 3D scenes: the globe header and the map.
- Every place carries the colour of its kind: saffron temples, sienna heritage, rani-pink culture, sea-green nature, and so on.
- Serif titles give it an editorial voice.
- Mono labels carry the data.
- Depth comes from the 3D globe, cards that tilt toward the pointer, and the tilted map with pillars.

---

## 3. Tokens

All tokens are on `:root` in `CSS`. Contrast ratios were computed with the WCAG formula; text pairs pass AA.

### 3.1 Neutrals and brand

| Token | Value | Role | Contrast |
|---|---|---|---|
| `--sand` | `#FBF7F1` | Page | n/a |
| `--sand-2` | `#F4EDE2` | Start stop, secondary fills | n/a |
| `--surface` | `#FFFFFF` | Cards, form, strip | n/a |
| `--line` / `--line-2` | `#ECE4D8` / `#E2D8C9` | Hairlines / input borders | n/a |
| `--ink` | `#1F2A2E` | Text | 13.8:1 on sand |
| `--ink2` | `#56636A` | Secondary text | ≈6.2:1 on white |
| `--ink3` | `#657276` | Mono labels | 4.97:1 white · 4.66:1 sand |
| `--peacock` | `#0F5C63` | **Brand:** primary button, selected chip, links, focus, step numbers | white on it 7.7:1; on sand 7.2:1 |
| `--peacock-2` / `--peacock-deep` | `#13777F` / `#0B3F45` | Button gradient top / night scenes | sand on deep 10.9:1 |
| `--peacock-tint` | `#E3F0EF` | Review panel | n/a |
| `--marigold` | `#F2A33A` | Accent: hero italic, globe pins, route start | 5.6:1 on deep peacock |
| `--marigold-tint` | `#FDF1DC` | Warning notice fill | n/a |
| `--terracotta` | `#B54D2A` | "Start here" eyebrow | 5.2:1 on white |
| Coral pin | `#FF7A59` | Chosen place on the globe, route end | n/a |

### 3.2 Category colours

Each category colour appears on the card art gradient (colour → colour at 85% → `#0B3F45`, 150°), the number pin, the map pillar and its glow, and the day-plan marker.

| Category | Colour | | Category | Colour |
|---|---|---|---|---|
| religious (Temple) | `#E07A1F` saffron | | beaches (Beach) | `#1C8DA6` sea |
| historical (Heritage) | `#A0522D` sienna | | waterfalls (Waterfall) | `#2F6FB0` river |
| cultural (Culture) | `#B83B5E` rani pink | | wildlife (Wildlife) | `#7A8F2B` olive |
| nature (Nature) | `#2E8B57` sea green | | adventure (Viewpoint) | `#C9862B` ochre |
| parks_gardens (Garden) | `#5A9E3A` leaf | | food (Food) | `#C0392B` chilli |
| shopping (Market) | `#8E44AD` plum | | entertainment (Leisure) | `#3F51B5` indigo |

### 3.3 Status (meaning only)

A status is a 7px dot inside a sand chip, and the label always states it. `--success #1E8A5A` (open, light traffic, good weather) · `--warning #C77A12` (opens later, moderate traffic, so-so weather) · `--danger #C63D2F` (closed, heavy traffic, poor weather, restricted) · `--neutral` (estimate).

### 3.4 Shape, depth and motion

| Group | Values |
|---|---|
| **Radius** | 12px (fields) · 14px (notices, review panel) · 18px (strip, stops) · **22px** (cards, form, hero, map) · 999px (search pill, chips, buttons) |
| **`--shadow`** | `0 1px 2px rgba(31,42,46,.06), 0 8px 24px -14px rgba(31,42,46,.28)` |
| **`--shadow-hi`** | `0 2px 4px rgba(31,42,46,.06), 0 22px 40px -18px rgba(11,63,69,.40)` |
| **Primary button** | 50px pill, `linear-gradient(180deg, #13777F, #0F5C63)`, lip `inset 0 -3px 0 rgba(0,0,0,.18)`, shadow `0 12px 24px -12px rgba(11,63,69,.65)` |
| **Motion** | `cubic-bezier(.2,.8,.2,1)`; .15s for chips and buttons, .35s for card tilt |

---

## 4. Typography

| Token | Stack | Role |
|---|---|---|
| `--serif` | `"Fraunces", Georgia, serif` (500–700, optical sizes 9–144) | Hero H1, step titles, results heading, card titles, stop names, plan time range |
| `--sans` | `"Hanken Grotesk", system-ui` | Body, chips, buttons |
| `--mono` | `"IBM Plex Mono"` | Step numbers, field labels, the "how we ranked" line, times, category kind, pins |

| Style | Font | Size / line-height | Weight | Notes |
|---|---|---|---|---|
| Hero H1 | serif | 48 / 49 (34 at ≤640px) | 600 | -0.025em; second line italic in marigold |
| Results heading | serif | 30 / 34 (24 at ≤640px) | 600 | "6 picks around Bhubaneswar" |
| Step title | serif | 22 / 28 | 600 | Preceded by a mono number `01` in peacock |
| Featured card title | serif | 28 / 32 | 600 | n/a |
| Card title | serif | 20 / 25 | 600 | n/a |
| Body / why | sans | 14 / 21 | 400 | n/a |
| Meta line | sans | 13 / 19 | 400 | `★ 4.4 (2,210) · Culture · 2 min drive` |
| Label | mono | 10.5 / 16 | 600 | Uppercase, 0.08em, `--ink3` |
| How-we-ranked | mono | 11 / 17 | 500 | Uppercase, 0.04em |

---

## 5. Page structure

1. **Hero (3D globe on peacock night).** Wordmark `radius` + `INDIA`. Headline "A day that fits, *wherever you are.*" Sub line: "Planning a day around {place}." The globe pins popular cities in marigold and the chosen place in coral, with a pulse and beam.
2. **`01 Where are you starting?`:** a pill search (54px), with popular-city chips while the field is empty.
3. **`02 Shape your day`:** one card:
   - **trip bar:** When · From · Hours · Within, in one row
   - chips: **You enjoy**, then **Travelling**
   - "More preferences" (free text, ticket cap)
   - **Show my day** button
4. **`03 Your day`:**
   - **Heading:** "N picks around {place}", a summary of the choices ("Fri 20 Nov, from 09:00 · 4 h · within 10 km · History · Temples"), and the how-we-ranked line.
   - **Conditions strip:** one card split into cells for Weather · your time, Air quality (coloured dot), Traffic · your start (when live) and Season.
   - **Notices:** festivals, and the restricted-temples note.
   - **Tabs** (underlined, as on booking sites):
     - **Places:** a featured **Top pick** card ("Start here") followed by a 2-column grid
     - **Day plan:** the itinerary
     - **3D map**
   - **"Not quite right?"** refine row.
5. **Footer credits:** sources, © CARTO, check locally.

---

## 6. Components

### 6.1 Place card (Airbnb anatomy, Tripadvisor trust, Wanderlog utility)

| Part | Spec |
|---|---|
| **Art** | 4:3. With a photo: the photo plus a dark fade at the bottom and a "Wikimedia Commons" credit. Without one: a **poster**, i.e. the category gradient, a giant serif initial (180px, white at 16%) and the kind in mono caps. Number pin top-left (30px circle in the category colour, white 2px ring). **At most one badge** top-right (white pill). |
| **Badges** (`badges()`) | **Top pick** = rank 1. **Best rated** = highest rating among picks with ≥50 ratings. **Closest** = shortest drive. **Good in this weather** = rain or storm in the forecast and an indoor place rated suitable. Each place gets at most one, in that priority order. Every badge is computed from data. |
| **Body** | Serif title. Meta line `★ rating (count) · Kind · N min drive (est.) · traffic`. Chips: opening status, weather fit, "**Plan about N min**" (planned visit, else the category default). One "why" line. Critical notices (entry rules) always visible. |
| **More about this place** | Native `<details>`, open by default on the featured card. Inside: the What visitors say panel (`+` likes, `−` dislikes, source label), hours today, ticket price, warnings, season note. |
| **Actions** | **Directions** (ink pill; peacock on hover) opens Google Maps navigation. "Read about it" and "Website" are underlined links. |
| **Featured** | Grid `1.15fr 1fr`, art at least 340px tall, title 28px, terracotta eyebrow **START HERE**, tilt capped at 3°. |
| **Depth** | `TILT_JS`: pointer tilt (6°, or 3° when featured), −3px lift, highlight following the pointer. Art at `translateZ(16px)`, body at `translateZ(24px)`. Off for touch and reduced motion. |

### 6.2 Day plan (Wanderlog pattern)

- **Header:** the time range in serif 22px, then "3 stops · about 10 min on the road, drive times estimated".
- **Start card:** on sand-2, showing the start time and place.
- **Between stops:** a dashed-border **leg**: "4 min drive", with `EST.` in mono when there's no live traffic.
- **Stop card:** grid `32px 1fr 92px`.
  - number marker in the category colour, matching the list and the map
  - time window `09:04 – 10:04` in mono peacock
  - serif name, then "60 min here · then waits N min for opening"
  - a 64px thumbnail (photo or category gradient)
- **Footer line:** "Didn't fit in your time: …".

### 6.3 3D map

| Part | Spec |
|---|---|
| Base | CARTO dark, pitch 58°, bearing −28°. The zoom fits the picks and the start point. |
| Pillars | `ColumnLayer` in category colours, with a glow ring at the base. Height grows with rating and rank. |
| **Numbers** | `TextLayer` at pillar top + 70 m, white 18px with a night outline. **They match the cards and the plan.** |
| Route | `ArcLayer`, marigold → coral, in plan order |
| Start | Peacock dot with a white ring |
| Legend | The categories present, plus Start and route. Then: "Numbers match the list. Taller pillars are better rated. Drag to rotate, scroll to zoom." |

### 6.4 Globe hero

three.js r128. Dots shade from teal `#3FB6AE` to marigold `#F2C46B`. City pins in marigold, the chosen place in coral `#FF7A59` with a pulse and beam. Marigold orbit ring at 35%. India faces the camera. The canvas is sized to the iframe (`width/height: 100%`; DPR ≤ 2). The globe stays still under reduced motion.

### 6.5 Inputs and controls

| Control | Spec |
|---|---|
| Search | 54px pill, white, 1px `--line-2`. Focus: peacock border plus a 3px ring at 12%. |
| Fields | 12px radius, white |
| Chips | 36px pills, white, 1px `--line-2`. Hover: ink border. **Selected: solid peacock, white text.** |
| Tabs | Underline style: ink text and a 2px ink indicator (react-aria `SelectionIndicator`) |
| Update | Outlined ink pill |

---

## 7. Responsive

| Width | Change |
|---|---|
| ≤720px | Grid becomes 1 column. Featured card stacks (art 220px). |
| ≤640px | Padding 14px. Hero H1 34px, globe below the copy. Results heading 24px. Strip 2-up. Stop thumbnails hidden. |
| `(hover: none)` / reduced motion | No tilt. Globe still. No transitions. |

Checked at 1024px and 375px (DPR 2): no horizontal scroll.

---

## 8. Copy rules (no AI slop)

1. Facts, not feelings: "4 min drive", "Opens at 10:00", "Plan about 75 min".
2. **No emoji.** Allowed symbols: `★`, `+`, `−`, `·`, and the italic hero line.
3. Sentence case. Mono labels are uppercase.
4. No exclamation marks or hype words ("perfect", "amazing", "hidden gem").
5. Name the source: "(est.)", "From 5 Google reviews · AI summary", "© CARTO", and the how-we-ranked line.
6. Badges and empty states must be explainable from data, and say what to do next.
7. Never show internals (models, fallbacks, scores, API errors). The one exception is the restricted-temples note.

---

## 9. Implementation map (Streamlit 1.67)

| Spec | Hook |
|---|---|
| Theme base | `.streamlit/config.toml`: `primaryColor #0F5C63`, `backgroundColor #FBF7F1`, `secondaryBackgroundColor #F4EDE2`, `textColor #1F2A2E` |
| Buttons | `[data-testid="stBaseButton-primaryFormSubmit"]`, `…-secondaryFormSubmit` |
| Chips | `[data-testid="stButtonGroup"] button`, selected `[aria-pressed="true"]` / `[aria-checked="true"]` |
| Tabs | `[data-testid="stTab"]`; underline = `.react-aria-SelectionIndicator` (recoloured, not hidden); `[role="tablist"]::after` hidden |
| Search pill | `[data-testid="stTextInputRootElement"]` |
| Cards / plan | `st.markdown(unsafe_allow_html=True)`. Every dynamic value goes through `esc()`. |
| Globe / tilt | `components.html(hero_html(...), height=330)` into an `st.empty()` slot; `components.html(TILT_JS, height=0)` |
| Map | `st.pydeck_chart(pdk.Deck(...), height=460)` |

---

## 10. Open questions

- **Photos.** Photo-first cards depend on images. Google Places photos need a server-side proxy so the API key stays private. Until then: Wikipedia images and the poster fallback.
- **Saving / sharing a day.** Wanderlog and Airbnb both centre on saving; Radius has no account or share link yet.
- **Weekday hours chips** (Wanderlog). These need the full weekly hours in the API response; today it exposes only "hours today".
- **Fonts.** Fraunces and Hanken Grotesk load from Google Fonts. Self-host for production?
- **React frontend.** Should it adopt the v3 tokens?
