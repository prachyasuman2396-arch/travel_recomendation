"""Convert OpenStreetMap ``opening_hours`` to the Google-style weekday text the rest of the app reads.

Covers the forms that make up the vast majority of tagged Indian attractions:

    24/7
    06:00-21:00                              (every day)
    Mo-Su 06:00-12:30,15:30-21:00
    Tu-Su 09:30-17:00; Mo off
    Mo-Fr 10:00-17:00; Sa 10:00-13:00; Su off
    Mo,We,Fr 09:00-17:00
    sunrise-sunset                           (approximated as 06:00-18:00, and labelled so)

Anything else (month ranges, week numbers, "PH", comments, "open end" …) returns ``None`` = unknown.
Unknown is always safer than a wrong "closed": the app shows "hours unknown" and never filters on it.
Spec: https://wiki.openstreetmap.org/wiki/Key:opening_hours
"""

from __future__ import annotations

import re

DAYS = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_DAY_SPEC = re.compile(r"^(Mo|Tu|We|Th|Fr|Sa|Su)(?:-(Mo|Tu|We|Th|Fr|Sa|Su))?$")
_TIME_RANGE = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")
APPROX_NOTE = "approximate (sunrise–sunset)"


def _days(spec: str) -> list[int] | None:
    out: list[int] = []
    for part in spec.split(","):
        m = _DAY_SPEC.match(part.strip())
        if not m:
            return None
        a = DAYS.index(m.group(1))
        b = DAYS.index(m.group(2)) if m.group(2) else a
        out.extend(range(a, b + 1) if a <= b else [*range(a, 7), *range(0, b + 1)])  # Fr-Mo wraps
    return out


def _clock(h: int, m: int) -> str:
    if h == 24 and m == 0:
        return "12:00 AM"
    suffix = "AM" if h < 12 else "PM"
    return f"{(h % 12) or 12}:{m:02d} {suffix}"


def _times(spec: str) -> str | None:
    """'06:00-12:30,15:30-21:00' -> '6:00 AM – 12:30 PM, 3:30 – 9:00 PM' style text (full form each side)."""
    spec = spec.strip().replace("sunrise", "06:00").replace("sunset", "18:00")
    parts = []
    for rng in spec.split(","):
        m = _TIME_RANGE.match(rng.strip())
        if not m:
            return None
        h1, m1, h2, m2 = (int(x) for x in m.groups())
        if not (0 <= h1 <= 24 and 0 <= h2 <= 24 and m1 < 60 and m2 < 60):
            return None
        if (h1, m1) == (0, 0) and (h2, m2) == (24, 0):
            return "Open 24 hours"
        if (h2 * 60 + m2) <= (h1 * 60 + m1):
            return None  # past midnight (e.g. 18:00-02:00): not needed for attractions; stay unknown
        parts.append(f"{_clock(h1, m1)} – {_clock(h2, m2)}")
    return ", ".join(parts) if parts else None


def osm_to_weekday_descriptions(value: str | None) -> dict | None:
    """Return ``{"weekdayDescriptions": [...], "osm": value, "approximate": bool}`` or None if not understood."""
    if not value or not value.strip():
        return None
    raw = value.strip()
    if raw == "24/7":
        return {"weekdayDescriptions": [f"{d}: Open 24 hours" for d in DAY_NAMES], "osm": raw, "approximate": False}
    approximate = "sunrise" in raw or "sunset" in raw
    week: list[str | None] = [None] * 7
    for rule in (r.strip() for r in raw.split(";")):
        if not rule:
            continue
        tokens = rule.split(None, 1)
        if len(tokens) == 1:  # "06:00-21:00" = every day, or "off"
            days, spec = list(range(7)), tokens[0]
        else:
            days = _days(tokens[0])
            spec = tokens[1]
            if days is None:
                return None
        spec = spec.strip()
        if spec in ("off", "closed"):
            text = "Closed"
        else:
            text = _times(spec)
            if text is None:
                return None
        for d in days:
            week[d] = text  # later rules override earlier ones, as in the OSM spec
    if all(t is None for t in week):
        return None
    lines = [f"{DAY_NAMES[i]}: {t}" for i, t in enumerate(week) if t is not None]
    return {"weekdayDescriptions": lines, "osm": raw, "approximate": approximate}
