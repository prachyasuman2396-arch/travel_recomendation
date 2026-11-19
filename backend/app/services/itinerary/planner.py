"""Turn top recommendations into a timed half-day plan.

Deterministic search (no LLM): try orders of the best-ranked places (≤ 6 → at most 1,956 permutations),
simulate the day with travel times, visit lengths and opening hours (including midday temple closures),
and keep the plan that fits the most places, then favours better-ranked ones, then less travel.
Travel times come from OSRM when configured, otherwise a labelled estimate.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.services.routing.router import TravelMatrix

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_TIME = re.compile(r"(\d{1,2}):(\d{2})\s*([AP]M)?", re.IGNORECASE)
Window = tuple[int, int]  # minutes since local midnight


def _minutes(h: int, m: int, ampm: str | None) -> int:
    if ampm:
        ampm = ampm.upper()
        if ampm == "PM" and h != 12:
            h += 12
        if ampm == "AM" and h == 12:
            h = 0
    return h * 60 + m


def parse_day_windows(opening_hours: dict | None, when: datetime) -> list[Window] | None:
    """Google ``weekdayDescriptions`` for the given day -> open windows. None = unknown, [] = closed.

    Handles '6:00 AM – 9:00 PM', '6:00 AM – 12:30 PM, 3:30 – 9:00 PM' (AM/PM inherited from the end
    time when the start omits it) and 'Open 24 hours'."""
    if not opening_hours:
        return None
    day = WEEKDAYS[when.weekday()]
    line = next((d for d in opening_hours.get("weekdayDescriptions") or [] if d.startswith(day + ":")), None)
    if line is None:
        return None
    text = line.split(":", 1)[1].strip().replace(" ", " ").replace(" ", " ")
    if re.fullmatch(r"closed", text, re.IGNORECASE):
        return []
    if "24 hours" in text.lower():
        return [(0, 24 * 60)]
    windows: list[Window] = []
    for part in text.split(","):
        times = _TIME.findall(part)
        if len(times) != 2:
            continue
        (h1, m1, ap1), (h2, m2, ap2) = times
        end = _minutes(int(h2), int(m2), ap2 or None)
        start = _minutes(int(h1), int(m1), ap1 or ap2 or None)
        if start > end and not ap1:  # e.g. '11:00 – 1:00 PM' -> 11 AM
            start = _minutes(int(h1), int(m1), "AM")
        if end <= start:  # past midnight
            end = 24 * 60
        windows.append((start, end))
    return windows or None


def subtract_closure(windows: list[Window] | None, closure: Window | None) -> list[Window] | None:
    if windows is None or closure is None:
        return windows
    out: list[Window] = []
    for s, e in windows:
        if closure[1] <= s or closure[0] >= e:
            out.append((s, e))
            continue
        if s < closure[0]:
            out.append((s, closure[0]))
        if closure[1] < e:
            out.append((closure[1], e))
    return out


@dataclass(frozen=True)
class StopInput:
    poi_id: str
    name: str
    latitude: float
    longitude: float
    visit_minutes: int
    windows: list[Window] | None  # None = hours unknown (assumed open, flagged)
    rank: int


@dataclass
class PlannedStop:
    poi_id: str
    name: str
    arrive: datetime
    depart: datetime
    travel_minutes: int
    wait_minutes: int
    visit_minutes: int
    hours_known: bool


@dataclass
class Itinerary:
    stops: list[PlannedStop]
    start: datetime
    end: datetime
    total_travel_minutes: int
    travel_method: str
    skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _simulate(order: tuple[int, ...], stops: list[StopInput], m: TravelMatrix, start_min: int, budget: int):
    t, travel_total, prev = start_min, 0.0, 0  # matrix index 0 = start point
    timeline = []
    for idx in order:
        stop = stops[idx]
        travel = m.minutes[prev][idx + 1]
        arrive = t + travel
        wait = 0.0
        if stop.windows is not None:
            window = next(((s, e) for s, e in stop.windows if arrive < e and max(arrive, s) + stop.visit_minutes <= e),
                          None)
            if window is None:
                return None
            wait = max(0.0, window[0] - arrive)
        depart = arrive + wait + stop.visit_minutes
        timeline.append((idx, travel, arrive + wait, depart, wait))
        travel_total += travel
        t, prev = depart, idx + 1
    back = m.minutes[prev][0]
    if t + back - start_min > budget:
        return None
    return timeline, travel_total + back, t + back


def plan_itinerary(stops: list[StopInput], matrix: TravelMatrix, start: datetime, available_minutes: int,
                   max_stops: int = 5) -> Itinerary:
    """``matrix`` indices: 0 = start point, i+1 = stops[i]. ``start`` is local time at the destination."""
    start_min = start.hour * 60 + start.minute
    pool = sorted(range(len(stops)), key=lambda i: stops[i].rank)[:6]
    best = None
    best_key = None
    for k in range(min(max_stops, len(pool)), 0, -1):
        for combo in itertools.combinations(pool, k):
            for order in itertools.permutations(combo):
                sim = _simulate(order, stops, matrix, start_min, available_minutes)
                if sim is None:
                    continue
                _, travel, _ = sim
                key = (k, sum(1.0 / stops[i].rank for i in order), -travel)
                if best_key is None or key > best_key:
                    best, best_key = (order, sim), key
        if best is not None:
            break  # the largest feasible number of stops wins
    midnight = start.replace(hour=0, minute=0, second=0, microsecond=0)
    if best is None:
        return Itinerary([], start, start, 0, matrix.method, skipped=[s.name for s in stops],
                         notes=["None of the top places fit in the available time with their opening hours."])
    order, (timeline, travel_total, end_min) = best
    planned = [PlannedStop(
        stops[i].poi_id, stops[i].name, midnight + timedelta(minutes=arrive), midnight + timedelta(minutes=depart),
        round(travel), round(wait), stops[i].visit_minutes, stops[i].windows is not None,
    ) for i, travel, arrive, depart, wait in timeline]
    chosen = set(order)
    notes = []
    if matrix.method == "estimate":
        notes.append("Travel times are estimates from straight-line distance, not a route.")
    if any(not p.hours_known for p in planned):
        notes.append("Some opening hours are unknown — check before you go.")
    return Itinerary(planned, start, midnight + timedelta(minutes=end_min), round(travel_total), matrix.method,
                     skipped=[s.name for i, s in enumerate(stops) if i not in chosen], notes=notes)
