"""Exploratory data analysis of the collected dataset.

Every statistic is computed from the database — nothing is estimated or filled in.
Outputs go to data/reports/eda_<timestamp>/:
  summary.json, report.md, *.png (Matplotlib) and, if Plotly is installed, map.html.

Usage:  python scripts/run_eda.py
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class EdaFrames:
    pois: pd.DataFrame      # poi_id, name, normalized_name, category, latitude, longitude, data_origin, description,...
    sources: pd.DataFrame   # poi_id, provider, matching_method, matching_confidence
    ratings: pd.DataFrame   # poi_id, source, rating, rating_count
    reviews: pd.DataFrame   # poi_id, source, rating, has_text
    photos: pd.DataFrame    # poi_id, match_confidence, match_method
    candidates: pd.DataFrame  # provider, score, status
    sentiment: pd.DataFrame  # poi_id, status, overall


def load_frames(engine) -> EdaFrames:
    q = lambda sql: pd.read_sql_query(sql, engine)  # noqa: E731
    return EdaFrames(
        pois=q("SELECT poi_id, name, normalized_name, category, latitude, longitude, data_origin, description, "
               "opening_hours IS NOT NULL AS has_hours, website IS NOT NULL AS has_website, address FROM pois"),
        sources=q("SELECT poi_id, provider, matching_method, matching_confidence FROM poi_sources"),
        ratings=q("SELECT poi_id, source, rating, rating_count FROM poi_ratings"),
        reviews=q("SELECT poi_id, source, rating, review_text IS NOT NULL AS has_text FROM reviews"),
        photos=q("SELECT poi_id, match_confidence, match_method FROM flickr_photos"),
        candidates=q("SELECT provider, score, status FROM match_candidates"),
        sentiment=q("SELECT poi_id, status, overall FROM poi_sentiment"),
    )


def _describe(series: pd.Series) -> dict:
    s = series.dropna()
    if s.empty:
        return {"count": 0}
    return {"count": int(s.size), "mean": round(float(s.mean()), 3), "median": round(float(s.median()), 3),
            "min": round(float(s.min()), 3), "max": round(float(s.max()), 3)}


def compute_summary(f: EdaFrames) -> dict:
    pois = f.pois
    n = len(pois)
    dup_names = int(pois.duplicated(subset=["normalized_name"], keep=False).sum()) if n else 0
    missing = {col: int(pois[col].isna().sum()) for col in ("description", "address") if col in pois}
    missing |= {"opening_hours": int((~pois["has_hours"]).sum()) if n else 0,
                "website": int((~pois["has_website"]).sum()) if n else 0,
                "coordinates": int(pois[["latitude", "longitude"]].isna().any(axis=1).sum()) if n else 0}
    photos_per_poi = f.photos.groupby("poi_id").size().reindex(pois["poi_id"], fill_value=0) if n else pd.Series()
    reviews_per_poi = f.reviews.groupby("poi_id").size().reindex(pois["poi_id"], fill_value=0) if n else pd.Series()
    coverage = f.sources.groupby("provider")["poi_id"].nunique().to_dict()
    multi = f.sources.groupby("poi_id")["provider"].nunique()
    auto = f.sources[f.sources["matching_method"] == "auto"]["matching_confidence"]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "data_origin": pois["data_origin"].value_counts().to_dict() if n else {},
        "total_destinations": n,
        "category_distribution": pois["category"].value_counts().to_dict() if n else {},
        "missing_values": missing,
        "possible_duplicate_names": dup_names,
        "rating_by_source": {src: _describe(g["rating"]) for src, g in f.ratings.groupby("source")},
        "rating_count_by_source": {src: _describe(g["rating_count"]) for src, g in f.ratings.groupby("source")},
        "reviews": {"total": len(f.reviews), "with_text": int(f.reviews["has_text"].sum()) if len(f.reviews) else 0,
                    "pois_with_reviews": int((reviews_per_poi > 0).sum()) if n else 0},
        "flickr_photos": {"total": len(f.photos), "pois_with_photos": int((photos_per_poi > 0).sum()) if n else 0,
                          "per_poi": _describe(photos_per_poi.astype(float)) if n else {},
                          "match_confidence": _describe(f.photos["match_confidence"])},
        "source_coverage": {"pois_per_provider": coverage,
                            "pois_with_multiple_providers": int((multi > 1).sum())},
        "entity_matching": {"auto_matches": int(auto.size), "auto_confidence": _describe(auto),
                            "flagged_for_review": int((f.candidates["status"] == "pending").sum())
                            if len(f.candidates) else 0},
        "sentiment_status": f.sentiment["status"].value_counts().to_dict() if len(f.sentiment) else {},
        "geography": {"lat": _describe(pois["latitude"]), "lon": _describe(pois["longitude"])} if n else {},
        "notes": ["Flickr photo counts measure photo activity, not visitor numbers.",
                  "Mock rows (data_origin=mock) are synthetic and must not be interpreted as real statistics."],
    }


def _bar(data: dict, title: str, path: Path, xlabel: str = "") -> None:
    fig, ax = plt.subplots(figsize=(8, 4))
    if data:
        keys = list(data)
        ax.barh(keys, [data[k] for k in keys], color="#2f6f5e")
        ax.invert_yaxis()
    else:
        ax.text(0.5, 0.5, "no data", ha="center", va="center")
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _hist(values: pd.Series, title: str, path: Path, bins: int = 10, log: bool = False) -> None:
    fig, ax = plt.subplots(figsize=(7, 4))
    v = values.dropna()
    if log:
        v = np.log10(v + 1)
    if v.empty:
        ax.text(0.5, 0.5, "no data", ha="center", va="center")
    else:
        ax.hist(v, bins=bins, color="#c8743a", edgecolor="white")
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def render_charts(f: EdaFrames, summary: dict, out: Path) -> list[str]:
    files = []
    _bar(summary["category_distribution"], "POIs by category", out / "categories.png", "POIs")
    _hist(f.ratings["rating"], "Rating distribution (all sources)", out / "ratings.png", bins=16)
    _hist(f.ratings["rating_count"], "Rating count (log10(n+1))", out / "rating_counts.png", log=True)
    _bar(summary["source_coverage"]["pois_per_provider"], "POIs linked per provider", out / "coverage.png", "POIs")
    _hist(f.photos["match_confidence"], "Flickr photo match confidence", out / "photo_confidence.png")
    _bar(summary["missing_values"], "Missing values per field", out / "missing.png", "POIs")
    files += ["categories.png", "ratings.png", "rating_counts.png", "coverage.png", "photo_confidence.png",
              "missing.png"]

    fig, ax = plt.subplots(figsize=(6, 6))
    if len(f.pois):
        for cat, g in f.pois.groupby("category"):
            ax.scatter(g["longitude"], g["latitude"], s=18, label=cat)
        ax.legend(fontsize=7, loc="best")
    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")
    ax.set_title("Geographic distribution")
    fig.tight_layout()
    fig.savefig(out / "geo.png", dpi=120)
    plt.close(fig)
    files.append("geo.png")

    try:
        import plotly.express as px
    except ImportError:
        logger.info("plotly not installed; skipping interactive map")
    else:
        if len(f.pois):
            fig = px.scatter_map(f.pois, lat="latitude", lon="longitude", color="category", hover_name="name",
                                 zoom=9) if hasattr(px, "scatter_map") else px.scatter_mapbox(
                f.pois, lat="latitude", lon="longitude", color="category", hover_name="name", zoom=9,
                mapbox_style="open-street-map")
            fig.write_html(out / "map.html", include_plotlyjs="cdn")
            files.append("map.html")
    return files


def write_report(f: EdaFrames, base_dir: Path) -> Path:
    out = base_dir / f"eda_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    out.mkdir(parents=True, exist_ok=True)
    summary = compute_summary(f)
    files = render_charts(f, summary, out)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    lines = ["# EDA report", "", f"Generated: {summary['generated_at']}", "",
             f"Data origin: `{summary['data_origin']}`", "", "```json", json.dumps(summary, indent=2, default=str),
             "```", ""] + [f"![{p}]({p})" for p in files if p.endswith(".png")]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return out
