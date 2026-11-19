"""Tiny offline gazetteer of Indian states and major cities, to catch mis-geotagged Wikipedia articles.

Some articles carry wrong coordinates: "Jagannath Temple, Bengaluru" ("Hindu temple in Karnataka, India")
is geotagged inside Puri, so GeoSearch returns it 550 m from the Jagannath Temple. The article itself says
where the place is — in the title suffix (", Bengaluru") or the description ("in Karnataka"). When that
stated location is a place we know and it is far from the coordinates, the coordinates are wrong.

Unknown names are ignored (no evidence either way). Bounding boxes are approximate on purpose and get a
margin, so border towns are never rejected.
"""

from __future__ import annotations

import math
import re

# (lat_min, lat_max, lon_min, lon_max)
STATES: dict[str, tuple[float, float, float, float]] = {
    "andhra pradesh": (12.6, 19.9, 76.7, 84.8), "arunachal pradesh": (26.6, 29.5, 91.5, 97.4),
    "assam": (24.1, 28.0, 89.7, 96.1), "bihar": (24.3, 27.6, 83.3, 88.3),
    "chhattisgarh": (17.8, 24.1, 80.2, 84.4), "goa": (14.9, 15.8, 73.6, 74.4),
    "gujarat": (20.1, 24.7, 68.1, 74.5), "haryana": (27.6, 30.9, 74.4, 77.6),
    "himachal pradesh": (30.4, 33.3, 75.5, 79.0), "jharkhand": (21.9, 25.4, 83.3, 87.9),
    "karnataka": (11.5, 18.5, 74.0, 78.6), "kerala": (8.2, 12.8, 74.8, 77.4),
    "madhya pradesh": (21.0, 26.9, 74.0, 82.8), "maharashtra": (15.6, 22.1, 72.6, 80.9),
    "manipur": (23.8, 25.7, 93.0, 94.8), "meghalaya": (25.0, 26.1, 89.8, 92.8),
    "mizoram": (21.9, 24.5, 92.2, 93.4), "nagaland": (25.2, 27.0, 93.3, 95.2),
    "odisha": (17.8, 22.6, 81.4, 87.5), "orissa": (17.8, 22.6, 81.4, 87.5),
    "punjab": (29.5, 32.5, 73.9, 76.9), "rajasthan": (23.0, 30.2, 69.5, 78.3),
    "sikkim": (27.1, 28.1, 88.0, 88.9), "tamil nadu": (8.0, 13.6, 76.2, 80.4),
    "telangana": (15.8, 19.9, 77.2, 81.3), "tripura": (22.9, 24.5, 91.1, 92.3),
    "uttar pradesh": (23.9, 30.4, 77.1, 84.6), "uttarakhand": (28.7, 31.5, 77.6, 81.1),
    "west bengal": (21.5, 27.2, 85.8, 89.9), "delhi": (28.4, 28.9, 76.8, 77.4),
    "jammu and kashmir": (32.3, 37.1, 73.3, 80.3), "ladakh": (32.3, 36.0, 75.3, 80.3),
    "chandigarh": (30.6, 30.8, 76.6, 76.9), "andaman and nicobar islands": (6.7, 13.7, 92.2, 94.0),
    "lakshadweep": (8.2, 12.4, 71.7, 74.0),
}

CITIES: dict[str, tuple[float, float]] = {
    "bengaluru": (12.97, 77.59), "bangalore": (12.97, 77.59), "mumbai": (19.08, 72.88), "bombay": (19.08, 72.88),
    "new delhi": (28.61, 77.21), "kolkata": (22.57, 88.36), "calcutta": (22.57, 88.36),
    "chennai": (13.08, 80.27), "madras": (13.08, 80.27), "hyderabad": (17.39, 78.49), "pune": (18.52, 73.86),
    "ahmedabad": (23.02, 72.57), "jaipur": (26.91, 75.79), "lucknow": (26.85, 80.95), "varanasi": (25.32, 83.01),
    "agra": (27.18, 78.01), "bhubaneswar": (20.30, 85.82), "cuttack": (20.46, 85.88), "puri": (19.81, 85.83),
    "sambalpur": (21.47, 83.97), "konark": (19.89, 86.09), "berhampur": (19.31, 84.79),
    "rourkela": (22.26, 84.85), "balasore": (21.49, 86.93), "kochi": (9.93, 76.27), "cochin": (9.93, 76.27),
    "thiruvananthapuram": (8.52, 76.94), "madurai": (9.93, 78.12), "mysuru": (12.30, 76.64),
    "mysore": (12.30, 76.64), "hampi": (15.34, 76.46), "udaipur": (24.59, 73.71), "jodhpur": (26.24, 73.02),
    "amritsar": (31.63, 74.87), "rishikesh": (30.09, 78.27), "haridwar": (29.95, 78.16),
    "darjeeling": (27.04, 88.26), "shillong": (25.58, 91.89), "guwahati": (26.14, 91.74), "patna": (25.59, 85.14),
    "ranchi": (23.34, 85.31), "bhopal": (23.26, 77.41), "indore": (22.72, 75.86), "nagpur": (21.15, 79.09),
    "visakhapatnam": (17.69, 83.22), "vijayawada": (16.51, 80.65), "tirupati": (13.63, 79.42),
    "coimbatore": (11.02, 76.96), "mangaluru": (12.91, 74.86), "mangalore": (12.91, 74.86),
    "surat": (21.17, 72.83), "vadodara": (22.31, 73.18), "dehradun": (30.32, 78.03), "srinagar": (34.08, 74.80),
    "leh": (34.15, 77.58), "bodh gaya": (24.70, 84.99), "ujjain": (23.18, 75.78), "mathura": (27.49, 77.67),
    "vrindavan": (27.58, 77.70), "ayodhya": (26.80, 82.20), "kanpur": (26.45, 80.33), "raipur": (21.25, 81.63),
}

CITY_RADIUS_KM = 60
STATE_MARGIN_DEG = 0.3  # ~33 km

_TITLE_SUFFIX = re.compile(r",\s*([^,()]+?)\s*$|\(([^()]+)\)\s*$")
# "Hindu temple in Karnataka, India" / "Pillar ... at Puri, Odisha" -> the place's own stated location
_DESC_LOCATION = re.compile(r"\b(?:in|at|near)\s+([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)*)")  # capitalised run only


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * 6371 * math.asin(math.sqrt(a))


def _stated_locations(title: str, description: str | None) -> list[str]:
    names: list[str] = []
    if m := _TITLE_SUFFIX.search(title):
        names.append((m.group(1) or m.group(2)).strip())
    if description:
        for part in description.split(","):  # "…in Puri town, Odisha (India)" -> "Puri town", "Odisha"
            names.extend(n.strip() for n in _DESC_LOCATION.findall(part))
            if (tail := part.strip()) and tail[:1].isupper() and len(tail.split()) <= 3:
                names.append(tail.removesuffix("(India)").strip())
    return [n.lower().removesuffix(" town").removesuffix(" city").strip() for n in names if n]


def location_conflict(title: str, description: str | None, lat: float, lon: float) -> str | None:
    """The stated location that contradicts the coordinates, or ``None`` if nothing known disagrees."""
    for name in _stated_locations(title, description):
        if (city := CITIES.get(name)) is not None:
            if _km(lat, lon, *city) > CITY_RADIUS_KM:
                return name
        elif (box := STATES.get(name)) is not None:
            lat_min, lat_max, lon_min, lon_max = box
            m = STATE_MARGIN_DEG
            if not (lat_min - m <= lat <= lat_max + m and lon_min - m <= lon <= lon_max + m):
                return name
    return None
