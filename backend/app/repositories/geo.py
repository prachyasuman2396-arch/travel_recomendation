"""PostGIS expression helpers (parameterised — coordinates are bound, never string-formatted)."""

from __future__ import annotations

from geoalchemy2 import Geography
from sqlalchemy import cast, func


def geography_point(latitude: float, longitude: float):
    # NB: ST_MakePoint takes (longitude, latitude) — x before y.
    return cast(func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326), Geography(srid=4326))
