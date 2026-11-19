"""Generate the MOCK sample dataset in data/sample/.

Everything written here is synthetic and labelled as such:
  * POI names/coordinates are real Bhubaneswar-area places, coordinates APPROXIMATE
  * descriptions, ratings, review counts, reviews and photos are INVENTED for offline development
  * files are in each provider's native response shape so the real parsers are exercised

The dataset deliberately contains hard cases for the pipeline:
  * a cafe ~25 m from a botanical garden (must NOT merge)
  * spelling variants across providers (Lingaraj / Lingaraja)
  * an ambiguous partial name (Udayagiri Caves vs Udayagiri and Khandagiri Caves)
  * invalid coordinates, a missing name, duplicate IDs, a permanently closed place
  * nearby but untagged photos (must not be associated)

Run:  python scripts/build_mock_dataset.py
"""

from __future__ import annotations

import json
import random
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "data" / "sample"
NOTICE = ("MOCK DATA for offline development. Coordinates approximate; descriptions, ratings, "
          "reviews and photos are synthetic and must never be presented as real.")

# (id, name, lat, lon, types, primaryType, rating, count, summary, address, website, hours)
PLACES = [
    ("mock_g_nandankanan", "Nandankanan Zoological Park", 20.3958, 85.8189, ["zoo", "tourist_attraction"], "zoo",
     4.4, 61234, "Large zoo and botanical garden beside Kanjia lake with white tigers and a safari.",
     "Barang, Bhubaneswar, Odisha 754005", "https://nandankanan.org", "zoo"),
    ("mock_g_lingaraj", "Lingaraj Temple", 20.2382, 85.8338, ["hindu_temple", "place_of_worship", "tourist_attraction"],
     "hindu_temple", 4.7, 48211, "11th-century Kalinga-style temple dedicated to Harihara.",
     "Lingaraj Rd, Old Town, Bhubaneswar, Odisha 751002", None, "temple"),
    ("mock_g_udayagiri", "Udayagiri and Khandagiri Caves", 20.2627, 85.7856,
     ["tourist_attraction", "historical_landmark"],
     "historical_landmark", 4.5, 27654, "Rock-cut Jain caves from the 2nd century BCE on twin hills.",
     "Khandagiri, Bhubaneswar, Odisha 751030", None, "daylight"),
    ("mock_g_dhauli", "Dhauli Shanti Stupa", 20.1925, 85.8395, ["tourist_attraction", "place_of_worship"],
     "tourist_attraction", 4.5, 22140, "White peace pagoda on Dhauli hill overlooking the Daya river.",
     "Dhauli, Bhubaneswar, Odisha 752104", None, "daylight"),
    ("mock_g_ekamra_kanan", "Ekamra Kanan Botanical Gardens", 20.3045, 85.8180, ["park", "tourist_attraction"],
     "park", 4.3, 15322, "Botanical garden with a rose garden, cactus house and a boating lake.",
     "Nayapalli, Bhubaneswar, Odisha 751015", None, "park"),
    ("mock_g_mukteswar", "Mukteswar Temple", 20.2427, 85.8418, ["hindu_temple", "place_of_worship"], "hindu_temple",
     4.6, 6120, "10th-century temple known for its carved torana arch.",
     "Kedar Gouri Vihar, Bhubaneswar, Odisha 751002", None, "temple"),
    ("mock_g_rajarani", "Rajarani Temple", 20.2440, 85.8436, ["hindu_temple", "tourist_attraction"], "hindu_temple",
     4.5, 5873, "Sandstone temple with no presiding deity, famed for sculpture.",
     "Tankapani Rd, Bhubaneswar, Odisha 751002", None, "daylight"),
    ("mock_g_state_museum", "Odisha State Museum", 20.2575, 85.8418, ["museum", "tourist_attraction"], "museum",
     4.3, 7012, "Archaeology, palm-leaf manuscripts and natural history galleries.",
     "Lewis Rd, Bhubaneswar, Odisha 751014", None, "museum"),
    ("mock_g_bindu_sagar", "Bindu Sagar Lake", 20.2399, 85.8359, ["tourist_attraction"], "tourist_attraction",
     4.2, 3102, "Sacred temple tank near Lingaraj surrounded by small shrines.",
     "Old Town, Bhubaneswar, Odisha 751002", None, None),
    ("mock_g_science_centre", "Regional Science Centre", 20.2985, 85.8228, ["museum", "tourist_attraction"], "museum",
     4.3, 9871, "Hands-on science galleries, a dinosaur park and a 3D theatre.",
     "Acharya Vihar, Bhubaneswar, Odisha 751013", None, "museum"),
    ("mock_g_tribal_museum", "Museum of Tribal Arts and Artefacts", 20.3006, 85.8261, ["museum"], "museum",
     4.4, 2210, "Collections of tribal dress, ornaments and implements of Odisha.",
     "CRP Square, Bhubaneswar, Odisha 751003", None, "museum"),
    ("mock_g_ekamra_haat", "Ekamra Haat", 20.2969, 85.8372, ["shopping_mall", "tourist_attraction"], "market",
     4.1, 8344, "Handicraft and handloom market with Odia crafts stalls.",
     "Madhusudan Nagar, Bhubaneswar, Odisha 751001", None, "market"),
    ("mock_g_esplanade", "Esplanade One Mall", 20.2908, 85.8586, ["shopping_mall"], "shopping_mall",
     4.3, 40876, "Large shopping mall with a multiplex and food court.",
     "Rasulgarh, Bhubaneswar, Odisha 751010", "https://example.invalid/esplanade", "mall"),
    ("mock_g_chandaka", "Chandaka Dampara Wildlife Sanctuary", 20.3260, 85.6950,
     ["national_park", "tourist_attraction"],
     "national_park", 4.2, 3540, "Forested sanctuary known for elephants and quiet trails.",
     "Chandaka, Bhubaneswar, Odisha 754005", None, "daylight"),
    ("mock_g_deras", "Deras Dam", 20.4060, 85.7090, ["tourist_attraction"], "tourist_attraction",
     4.3, 2790, "Reservoir inside the Chandaka forest, popular for sunsets and picnics.",
     "Deras, Odisha 752054", None, None),
    ("mock_g_hirapur", "Chausath Yogini Temple Hirapur", 20.2213, 85.8768, ["hindu_temple", "historical_landmark"],
     "hindu_temple", 4.6, 2633, "Circular hypaethral temple of 64 yoginis.",
     "Hirapur, Bhubaneswar, Odisha 752100", None, "daylight"),
    ("mock_g_atri", "Atri Hot Spring", 20.1600, 85.5100, ["tourist_attraction"], "tourist_attraction",
     4.0, 1890, "Sulphur hot spring with a small Hatakeswar temple.",
     "Atri, Khordha, Odisha 752050", None, None),
    ("mock_g_pipli", "Pipli Applique Village", 20.1150, 85.8320, ["tourist_attraction", "store"], "market",
     4.2, 1544, "Village famous for colourful applique craft shops along the highway.",
     "Pipli, Puri, Odisha 752104", None, "market"),
    ("mock_g_kalinga_stadium", "Kalinga Stadium", 20.2867, 85.8231, ["stadium", "tourist_attraction"], "stadium",
     4.5, 17234, "International hockey and athletics stadium.",
     "Nayapalli, Bhubaneswar, Odisha 751012", None, None),
    ("mock_g_ram_mandir", "Ram Mandir Bhubaneswar", 20.2867, 85.8415, ["hindu_temple", "place_of_worship"],
     "hindu_temple", 4.6, 19854, "Prominent city temple with tall spires on Janpath.",
     "Janpath, Kharvela Nagar, Bhubaneswar, Odisha 751001", None, "temple"),
    ("mock_g_barunei", "Barunei Hill", 20.1630, 85.6360, ["hiking_area", "tourist_attraction"], "hiking_area",
     4.4, 980, "Hill with a temple, stone steps and a seasonal stream for short hikes.",
     "Khordha, Odisha 752055", None, None),
    ("mock_g_puri_beach", "Puri Beach", 19.7983, 85.8249, ["tourist_attraction", "beach"], "beach",
     4.3, 52011, "Long sandy beach on the Bay of Bengal (about 55 km from Bhubaneswar).",
     "Puri, Odisha 752001", None, None),
    ("mock_g_jagannath", "Shree Jagannath Temple Puri", 19.8048, 85.8180, ["hindu_temple", "tourist_attraction"],
     "hindu_temple", 4.7, 210334, "12th-century temple of Lord Jagannath and starting point of the Rath Yatra.",
     "Grand Road, Puri, Odisha 752001", None, None),
    ("mock_g_konark", "Konark Sun Temple", 19.8876, 86.0945, ["hindu_temple", "historical_landmark"],
     "historical_landmark", 4.6, 98012, "13th-century chariot-shaped temple, a UNESCO World Heritage Site.",
     "Konark, Odisha 752111", None, "daylight"),
]

HOURS = {
    "zoo": ["Monday: Closed", "Tuesday: 7:30 AM – 5:30 PM", "Wednesday: 7:30 AM – 5:30 PM",
            "Thursday: 7:30 AM – 5:30 PM", "Friday: 7:30 AM – 5:30 PM", "Saturday: 7:30 AM – 5:30 PM",
            "Sunday: 7:30 AM – 5:30 PM"],
    "temple": [f"{d}: 6:00 AM – 9:00 PM" for d in
               ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")],
    "daylight": [f"{d}: 6:00 AM – 6:00 PM" for d in
                 ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")],
    "park": [f"{d}: 7:00 AM – 7:00 PM" for d in
             ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")],
    "museum": ["Monday: Closed"] + [f"{d}: 10:00 AM – 5:00 PM" for d in
                                     ("Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")],
    "market": [f"{d}: 10:00 AM – 9:00 PM" for d in
               ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")],
    "mall": [f"{d}: 10:00 AM – 10:00 PM" for d in
             ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")],
}


def build_places() -> list[dict]:
    places = []
    for pid, name, lat, lon, types, ptype, rating, count, summary, address, website, hours in PLACES:
        place = {
            "id": pid, "displayName": {"text": name, "languageCode": "en"}, "formattedAddress": address,
            "location": {"latitude": lat, "longitude": lon}, "types": types, "primaryType": ptype,
            "editorialSummary": {"text": summary, "languageCode": "en"}, "rating": rating,
            "userRatingCount": count, "businessStatus": "OPERATIONAL",
        }
        if website:
            place["websiteUri"] = website
        if hours:
            place["regularOpeningHours"] = {"weekdayDescriptions": HOURS[hours]}
        if pid in GOOGLE_REVIEWS:
            place["reviews"] = [
                {"name": f"places/{pid}/reviews/r{i}", "rating": r, "text": {"text": t, "languageCode": lang},
                 "publishTime": f"2026-0{(i % 9) + 1}-1{i}T09:30:00Z",
                 "authorAttribution": {"displayName": f"Mock Reviewer {i + 1}", "uri": ""}}
                for i, (r, t, lang) in enumerate(GOOGLE_REVIEWS[pid])
            ]
        if pid in ACCESSIBILITY:
            place["accessibilityOptions"] = {"wheelchairAccessibleEntrance": ACCESSIBILITY[pid]}
        if pid in GOOD_FOR_CHILDREN:
            place["goodForChildren"] = True
        # Mock photo references cannot be resolved by Google; the UI falls back to placeholders.
        place["photos"] = [{"name": f"places/{pid}/photos/mock1", "widthPx": 1200, "heightPx": 800,
                            "authorAttributions": [{"displayName": "Mock Photographer"}]}]
        places.append(place)
    # --- deliberate bad records ---
    places.append({"id": "mock_g_no_name", "location": {"latitude": 20.27, "longitude": 85.84}, "types": ["park"]})
    places.append({"id": "mock_g_bad_coords", "displayName": {"text": "Broken Coordinates Park"},
                   "location": {"latitude": 95.0, "longitude": 85.84}, "types": ["park"]})
    places.append({"id": "mock_g_closed", "displayName": {"text": "Old Toy Train Park"},
                   "location": {"latitude": 20.2801, "longitude": 85.8301}, "types": ["amusement_park"],
                   "businessStatus": "CLOSED_PERMANENTLY", "rating": 3.1, "userRatingCount": 40})
    places.append(dict(places[0]))  # duplicate external id
    return places


# (business id, name, lat, lon, categories, rating, review_count, reviews)
# Synthetic Google-style reviews (incl. Hinglish) — invented for offline development.
GOOGLE_REVIEWS = {
    "mock_g_nandankanan": [
        (5, "White tigers dekhne layak hai. Kids loved the safari, bahut maza aaya.", "hi-Latn"),
        (4, "Huge campus and green, but very crowded on Sundays. Carry water.", "en"),
        (3, "Nice place but food stalls are poor and toilets not clean.", "en"),
        (5, "Best zoo in Odisha. Go early morning, it is calm and animals are active.", "en"),
    ],
    "mock_g_lingaraj": [
        (5, "Peaceful early morning darshan. Phones not allowed inside, lockers available.", "en"),
        (4, "Bahut purana aur sundar mandir. Crowd zyada hai on Mondays.", "hi-Latn"),
        (2, "Pandas ask for money. Very crowded and difficult for elderly.", "en"),
    ],
    "mock_g_ekamra_kanan": [
        (5, "Serene garden with lovely rose section. Great for a quiet evening walk.", "en"),
        (4, "Clean and well maintained, boating is fun for kids.", "en"),
        (4, "Peaceful jagah, but parking thoda mushkil hai weekends pe.", "hi-Latn"),
    ],
    "mock_g_udayagiri": [
        (5, "Stunning carvings and views. Steep steps, wear good shoes.", "en"),
        (4, "Monkeys are naughty, keep food in bag. Peaceful in the morning.", "en"),
        (3, "Not wheelchair friendly at all, lots of climbing.", "en"),
    ],
    "mock_g_dhauli": [
        (5, "Very calm and scenic, beautiful sunset over the Daya river.", "en"),
        (5, "Peaceful, clean, fresh air. Light and sound show is nice.", "en"),
        (4, "Limited food options nearby but worth the drive.", "en"),
    ],
    "mock_g_deras": [
        (5, "Quiet dam inside the forest, amazing sunset. Very few people on weekdays.", "en"),
        (4, "Scenic and peaceful but no facilities, carry your own food and water.", "en"),
        (4, "Monsoon mein bahut sundar lagta hai. Road is a bit rough.", "hi-Latn"),
    ],
    "mock_g_konark": [
        (5, "Breathtaking architecture. Hire an ASI guide to understand the carvings.", "en"),
        (4, "Very hot in the afternoon, go early. Crowded during the December festival.", "en"),
        (4, "Clean and well maintained campus, ticket counter queue is long.", "en"),
    ],
}
ACCESSIBILITY = {"mock_g_esplanade": True, "mock_g_state_museum": True, "mock_g_udayagiri": False,
                 "mock_g_dhauli": False, "mock_g_science_centre": True, "mock_g_ekamra_kanan": True}
GOOD_FOR_CHILDREN = {"mock_g_nandankanan", "mock_g_science_centre", "mock_g_ekamra_kanan", "mock_g_esplanade"}

YELP = [
    ("mock_y_nandankanan", "Nandankanan Zoo", 20.3965, 85.8178, ["zoos"], 4.5, 38, [
        (5, "Beautiful green campus and the white tigers were amazing. Clean paths and plenty of shade."),
        (3, "Lovely environment but very crowded on Sunday and the food stalls were poor."),
        (4, "Great for families. The safari was fun though the queue was long."),
    ]),
    ("mock_y_lingaraja", "Lingaraja Temple", 20.2384, 85.8335, ["hindu_temples", "landmarks"], 4.5, 51, [
        (5, "Breathtaking architecture and a peaceful atmosphere early in the morning."),
        (2, "Pandas pushed for money and the crowd was overwhelming. Not very accessible for elderly visitors."),
        (4, "Must visit for history lovers. Photography is not allowed inside."),
    ]),
    ("mock_y_ekamra_cafe", "Ekamra Kanan Cafe", 20.3047, 85.8182, ["cafes", "coffee"], 3.8, 12, [
        (4, "Nice coffee after a walk in the garden. Friendly staff."),
        (3, "Average snacks, slow service, but the seating is pleasant."),
    ]),
    ("mock_y_dalma", "Dalma Restaurant", 20.2960, 85.8442, ["indpak", "restaurants"], 4.4, 64, [
        (5, "Authentic Odia thali with delicious dalma and great value."),
        (4, "Tasty food, clean tables, a bit noisy at lunch."),
        (5, "Best place to try local cuisine. Staff explained every dish."),
    ]),
    ("mock_y_state_museum", "Odisha State Museum", 20.2578, 85.8416, ["museums"], 4.0, 9, [
        (4, "Fascinating palm-leaf manuscripts. Some galleries were dusty and poorly lit."),
        (4, "Quiet and informative, good for a rainy afternoon."),
        (3, "Interesting collection but facilities like toilets need improvement."),
    ]),
    ("mock_y_udaygiri", "Udayagiri Caves", 20.2633, 85.7869, ["landmarks", "hiking"], 4.5, 22, [
        (5, "Fantastic views from the top and the carvings are stunning. Wear good shoes."),
        (4, "Monkeys everywhere, keep your food safe. Peaceful in the morning."),
        (4, "Steep steps, not great for wheelchair users, but worth the climb."),
    ]),
    ("mock_y_dhauli_pagoda", "Dhauli Peace Pagoda", 20.1929, 85.8391, ["landmarks", "buddhist_temples"], 4.5, 17, [
        (5, "Serene and calm, beautiful sunset over the river."),
        (4, "Peaceful place with fresh air. Limited food options nearby."),
        (5, "Clean, quiet and scenic. Great photography spot."),
    ]),
    ("mock_y_esplanade", "Esplanade One", 20.2910, 85.8583, ["shoppingcenters"], 4.1, 30, [
        (4, "Good brands and a clean food court. Parking can be slow."),
        (3, "Crowded on weekends, but fine for a movie."),
        (4, "Air-conditioned escape from the heat with decent restaurants."),
    ]),
    ("mock_y_rajarani_far", "Raja Rani Temple", 20.2474, 85.8436, ["landmarks"], 4.4, 11, [
        (5, "Beautiful carvings, very quiet compared to the big temples."),
        (4, "Small ticket fee, well maintained lawns."),
    ]),
    ("mock_y_no_coords", "Mystery Business", None, None, ["restaurants"], 3.0, 2, []),
]

# (photo id, owner, lat, lon, title, tags)  — clusters around POIs with tags, plus noise
PHOTO_CLUSTERS = [
    ("nandankanan", 20.3958, 85.8189, ["nandankanan", "zoo", "whitetiger", "odisha", "wildlife"], 9),
    ("lingaraj", 20.2382, 85.8338, ["lingaraj", "temple", "bhubaneswar", "kalinga", "architecture"], 8),
    ("udayagiri", 20.2627, 85.7856, ["udayagiri", "khandagiri", "caves", "jain", "history"], 7),
    ("dhauli", 20.1925, 85.8395, ["dhauli", "shantistupa", "peacepagoda", "sunset"], 6),
    ("ekamrakanan", 20.3045, 85.8180, ["ekamrakanan", "garden", "flowers", "nature"], 6),
    ("rajarani", 20.2440, 85.8436, ["rajarani", "temple", "sculpture"], 4),
    ("deras", 20.4060, 85.7090, ["derasdam", "deras", "lake", "sunset", "chandaka"], 5),
    ("hirapur", 20.2213, 85.8768, ["hirapur", "yogini", "chausathyogini", "temple"], 3),
    ("konark", 19.8876, 86.0945, ["konark", "suntemple", "unesco"], 5),
]


def build_photos() -> dict:
    rng = random.Random(42)
    photos = []
    n = 0
    for key, lat, lon, tags, count in PHOTO_CLUSTERS:
        for i in range(count):
            n += 1
            plat = lat + rng.uniform(-0.0012, 0.0012)
            plon = lon + rng.uniform(-0.0012, 0.0012)
            chosen = rng.sample(tags, k=min(len(tags), rng.randint(2, 4)))
            photos.append({
                "id": f"mock_f_{n:04d}", "owner": f"mock_owner_{rng.randint(1, 30):02d}",
                "ownername": f"Mock Photographer {rng.randint(1, 30)}",
                "title": f"{key.title()} visit {i + 1}", "tags": " ".join(chosen),
                "latitude": round(plat, 6), "longitude": round(plon, 6), "accuracy": 16,
                "dateupload": str(1_600_000_000 + rng.randint(0, 150_000_000)),
                "datetaken": "2023-01-15 09:30:00", "license": str(rng.choice([1, 2, 4, 5])),
                # No real image exists for mock photos; the UI renders a placeholder.
                "url_m": None,
            })
    # Untagged photos right next to POIs: proximity alone must not associate them.
    for i, (lat, lon) in enumerate([(20.3046, 85.8183), (20.2383, 85.8340), (20.2909, 85.8585)]):
        photos.append({"id": f"mock_f_untagged_{i}", "owner": "mock_owner_99", "title": "IMG_20230101",
                       "tags": "", "latitude": lat, "longitude": lon, "accuracy": 16,
                       "dateupload": "1650000000", "datetaken": "2022-04-15 10:00:00", "license": "0"})
    # Broken records
    photos.append({"id": "mock_f_badgeo", "owner": "x", "title": "no geo", "tags": "temple"})
    photos.append({"id": "mock_f_zero", "owner": "x", "title": "null island", "tags": "lingaraj",
                   "latitude": 0, "longitude": 0, "accuracy": 1})
    return {"photos": {"page": 1, "pages": 1, "perpage": 250, "total": len(photos), "photo": photos}, "stat": "ok"}


def build_yelp() -> tuple[dict, dict]:
    businesses, reviews = [], {}
    for bid, name, lat, lon, cats, rating, count, revs in YELP:
        businesses.append({
            "id": bid, "name": name, "coordinates": {"latitude": lat, "longitude": lon},
            "categories": [{"alias": c, "title": c.replace("_", " ").title()} for c in cats],
            "rating": rating, "review_count": count, "is_closed": False,
            "url": "https://example.invalid/yelp/" + bid,
            "location": {"display_address": ["Bhubaneswar, Odisha"]},
        })
        reviews[bid] = {"reviews": [
            {"id": f"{bid}_r{i}", "rating": r, "text": text, "time_created": f"2025-0{(i % 9) + 1}-12 10:15:00"}
            for i, (r, text) in enumerate(revs)
        ], "total": len(revs)}
    return {"businesses": businesses, "total": len(businesses)}, reviews


def build_weather() -> dict:
    # Open-Meteo-shaped payload (current + hourly), clearly mock.
    return {
        "latitude": 20.2961, "longitude": 85.8245, "timezone": "Asia/Kolkata",
        "current": {"time": "2026-11-16T10:00", "temperature_2m": 27.4, "relative_humidity_2m": 68,
                    "weather_code": 2, "wind_speed_10m": 9.4, "precipitation": 0.0},
        "hourly": {"time": [f"2026-11-16T{h:02d}:00" for h in range(10, 22)],
                   "precipitation_probability": [5, 5, 10, 10, 15, 20, 20, 15, 10, 5, 5, 5],
                   "temperature_2m": [27.4, 28.3, 29.0, 29.4, 29.5, 29.1, 28.2, 27.0, 26.1, 25.4, 24.9, 24.5],
                   "weather_code": [2, 2, 2, 3, 3, 3, 2, 1, 1, 0, 0, 0]},
    }


def build_osm() -> dict:
    """Overpass API JSON. Includes Odia names, Wikipedia/Wikidata links and OSM-only small temples."""
    def node(i, lat, lon, tags):
        return {"type": "node", "id": i, "lat": lat, "lon": lon, "tags": tags}

    def way(i, lat, lon, tags):
        return {"type": "way", "id": i, "center": {"lat": lat, "lon": lon}, "tags": tags}

    elements = [
        way(9001, 20.2381, 85.8337, {"name": "Lingaraj Temple", "name:or": "ଲିଙ୍ଗରାଜ ମନ୍ଦିର",
                                    "name:hi": "लिंगराज मंदिर", "amenity": "place_of_worship", "religion": "hindu",
                                    "historic": "temple", "wikipedia": "en:Lingaraja Temple",
                                    "wikidata": "Q_mock_lingaraj", "heritage": "2"}),
        way(9002, 20.3043, 85.8178, {"name": "Ekamra Kanan", "name:or": "ଏକାମ୍ର କାନନ", "leisure": "park",
                                    "wheelchair": "yes", "fee": "yes"}),
        node(9003, 20.2438, 85.8382, {"name": "Parasurameswara Temple", "name:or": "ପରଶୁରାମେଶ୍ୱର ମନ୍ଦିର",
                                      "amenity": "place_of_worship", "religion": "hindu", "historic": "temple",
                                      "wikipedia": "en:Parashurameshvara Temple",
                                      "wikidata": "Q_mock_parasurameswara"}),
        node(9004, 20.2422, 85.8478, {"name": "Brahmeswara Temple", "amenity": "place_of_worship",
                                      "religion": "hindu", "historic": "temple"}),
        node(9005, 20.2377, 85.8371, {"name": "Ananta Vasudeva Temple", "amenity": "place_of_worship",
                                      "religion": "hindu", "historic": "temple", "denomination": "vaishnavism"}),
        node(9006, 19.8049, 85.8179, {"name": "Jagannath Temple", "name:or": "ଶ୍ରୀ ଜଗନ୍ନାଥ ମନ୍ଦିର",
                                      "amenity": "place_of_worship", "religion": "hindu",
                                      "wikipedia": "en:Jagannath Temple, Puri", "wikidata": "Q_mock_jagannath"}),
        node(9007, 19.8877, 86.0946, {"name": "Konark Sun Temple", "historic": "archaeological_site",
                                      "tourism": "attraction", "heritage": "1",
                                      "wikipedia": "en:Konark Sun Temple", "wikidata": "Q_mock_konark"}),
        node(9008, 20.2629, 85.7857, {"name": "Udayagiri Caves", "historic": "archaeological_site",
                                      "tourism": "attraction", "wikipedia": "en:Udayagiri and Khandagiri Caves",
                                      "wikidata": "Q_mock_udayagiri"}),
        node(9009, 20.1931, 85.8401, {"name": "Dhauli Hill Viewpoint", "tourism": "viewpoint"}),
        node(9010, 20.2711, 85.8306, {"tourism": "attraction"}),  # no name -> rejected
    ]
    return {"version": 0.6, "generator": "mock-overpass", "elements": elements}


# Paraphrased, synthetic "Wikipedia-like" pages for offline fact extraction. NOT Wikipedia text.
WIKI = {
    "Lingaraja Temple": (
        "Lingaraja Temple is an 11th-century Hindu temple in Bhubaneswar dedicated to Harihara.",
        "Lingaraja Temple is the largest temple in Bhubaneswar and a major pilgrimage centre. "
        "The temple compound is not open to non-Hindus, but there is a viewing platform beside the wall. "
        "Mobile phones and cameras are not allowed inside the temple. "
        "Large crowds gather during Maha Shivaratri. Visitors are expected to dress modestly.",
    ),
    "Parashurameshvara Temple": (
        "Parashurameshvara Temple is a 7th-century Shiva temple in Bhubaneswar.",
        "Parashurameshvara Temple is one of the oldest surviving temples in Bhubaneswar. "
        "It is a small temple and a visit usually takes about 30 minutes. "
        "Photography of the exterior carvings is allowed.",
    ),
    "Jagannath Temple, Puri": (
        "The Jagannath Temple in Puri is a 12th-century temple dedicated to Jagannath.",
        "The Jagannath Temple is one of the Char Dham pilgrimage sites. "
        "Entry inside the temple is allowed only to Hindus. "
        "The annual Rath Yatra chariot festival starts from this temple and draws very large crowds. "
        "Mobile phones, cameras and leather items are not allowed inside the temple.",
    ),
    "Konark Sun Temple": (
        "Konark Sun Temple is a 13th-century temple built as a giant stone chariot for the Sun god.",
        "Konark Sun Temple is a UNESCO World Heritage Site managed by the Archaeological Survey of India. "
        "The entry fee is Rs 40 for Indian visitors and Rs 600 for foreign visitors. "
        "The Konark Dance Festival is held here every December. "
        "The site is open from sunrise to sunset and a visit usually takes about 2 hours.",
    ),
    "Udayagiri and Khandagiri Caves": (
        "Udayagiri and Khandagiri are partly natural and partly artificial caves of religious and historical "
        "importance near Bhubaneswar.",
        "The caves were carved for Jain monks in the 2nd century BCE. "
        "Reaching the upper caves involves climbing steep steps, so the site is not wheelchair accessible. "
        "Monkeys are common, so visitors should keep food packed away.",
    ),
}


def build_wikipedia() -> dict:
    pages = {}
    for title, (summary, text) in WIKI.items():
        pages[title] = {
            "title": title, "extract": summary, "text": text,
            "url": "https://en.wikipedia.org/wiki/" + title.replace(" ", "_"),
            "license": "CC BY-SA 4.0", "image": None,
        }
    return {"pages": pages, "wikidata_titles": {
        "Q_mock_lingaraj": "Lingaraja Temple", "Q_mock_parasurameswara": "Parashurameshvara Temple",
        "Q_mock_jagannath": "Jagannath Temple, Puri", "Q_mock_konark": "Konark Sun Temple",
        "Q_mock_udayagiri": "Udayagiri and Khandagiri Caves",
    }}


def build_air_quality() -> dict:
    return {"latitude": 20.2961, "longitude": 85.8245, "timezone": "Asia/Kolkata",
            "current": {"time": "2026-11-16T10:00", "us_aqi": 112, "pm2_5": 41.3, "pm10": 78.2}}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    yelp_search, yelp_reviews = build_yelp()
    files = {
        "places_nearby.json": {"_notice": NOTICE, "places": build_places()},
        "flickr_photos.json": {"_notice": NOTICE, **build_photos()},
        "yelp_businesses.json": {"_notice": NOTICE, **yelp_search},
        "yelp_reviews.json": {"_notice": NOTICE, "by_business": yelp_reviews},
        "weather_open_meteo.json": {"_notice": NOTICE, **build_weather()},
        "air_quality_open_meteo.json": {"_notice": NOTICE, **build_air_quality()},
        "osm_overpass.json": {"_notice": NOTICE, **build_osm()},
        "wikipedia_pages.json": {"_notice": NOTICE + " Page texts are short paraphrases written for testing, "
                                 "not copies of Wikipedia.", **build_wikipedia()},
    }
    for name, payload in files.items():
        (OUT / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {OUT / name}")


if __name__ == "__main__":
    main()
