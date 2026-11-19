# Travel times and routing

The itinerary planner needs a travel-time matrix between the user's start point and the shortlisted places.
`app/services/routing/router.py` provides two implementations behind one `RoutingService`:

| Router | When used | What it returns | Labelled as |
|---|---|---|---|
| `OSRMRouter` | `OSRM_URL` is set and the OSRM `/table/v1/driving` call succeeds | Road-network durations and distances | `osrm` |
| `EstimateRouter` | No `OSRM_URL`, or OSRM fails/times out | straight-line distance × `ROAD_DETOUR_FACTOR` (1.35) at `ASSUMED_AVG_SPEED_KMH` (22 km/h) | `estimate` |

Every itinerary carries `travel_method`, and the UI shows "estimated" times whenever the estimate was used.
The estimate is deliberately conservative for Indian city traffic. It is **not** a route and does not know about
one-way streets, river crossings or temple-area vehicle restrictions.

## Running OSRM locally (optional)

OSRM needs a pre-processed OpenStreetMap extract. For Odisha, use the Geofabrik *India – Eastern Zone* extract
(check https://download.geofabrik.de/asia/india.html for the current file names).

```bash
mkdir -p data/osrm && cd data/osrm
# 1. download the .osm.pbf extract from Geofabrik into this folder
# 2. pre-process (car profile, MLD pipeline)
docker run -t -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend:v5.27.1 osrm-extract -p /opt/car.lua /data/eastern-zone-latest.osm.pbf
docker run -t -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend:v5.27.1 osrm-partition /data/eastern-zone-latest.osrm
docker run -t -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend:v5.27.1 osrm-customize /data/eastern-zone-latest.osrm
cd ../..
# 3. start it with the compose profile and point the backend at it
docker compose --profile routing up -d osrm
echo "OSRM_URL=http://osrm:5000" >> .env && docker compose up -d backend
```

Pre-processing a regional extract needs several GB of RAM. These steps were **not** run while building this
project; the OSRM client is covered by unit tests with a mocked HTTP transport only.

Do not point `OSRM_URL` at the public demo server (`router.project-osrm.org`) in production: its usage policy
forbids heavy use.
