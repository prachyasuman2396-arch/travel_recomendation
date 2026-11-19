"""Offline evaluation harness.

Runs every scenario in ``scenarios.json`` through the real ``RecommendationEngine`` over the
knowledge base built by :func:`app.eval.offline.build_offline_store`, then scores:

* **hard violations** (must be 0) — checked against the *data*, not the model's words:
  beyond max distance, closed today, avoided category, verified fee above cap, verified
  wheelchair-inaccessible, verified non-Hindu no-entry without a viewing platform, a verified
  entry restriction shown without its notice, a cited fact id that does not exist, an itinerary
  longer than the available time.
* **expectations** — per-scenario checks (include/exclude places, understood constraints, events, season).
* **grounding** — share of recommendations that cite ≥1 sourced fact when the place has facts.
* **latency** — p50/p95 of the engine's own ``timings_ms.total``.

The LLM is the deterministic mock by default, so results are reproducible; ``--llm configured``
uses the Gemini/Groq chain from settings (costs tokens, results vary).
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from app.schemas.preference import PreferenceProfile
from app.schemas.recommendation import RecommendationResult
from app.services.feature_engineering.features import hours_for_day
from app.services.india.facts import facts_by_key
from app.services.india.rules import entry_fee_for
from app.services.recommendation.engine import RecommendationEngine
from app.services.recommendation.types import CandidateRow

SCENARIOS_PATH = Path(__file__).with_name("scenarios.json")


@dataclass
class ScenarioResult:
    id: str
    description: str
    passed: bool
    violations: list[str] = field(default_factory=list)
    failed_expectations: list[str] = field(default_factory=list)
    recommended: list[str] = field(default_factory=list)
    grounded: int = 0
    groundable: int = 0
    latency_ms: float = 0.0
    generation_method: str = ""
    error: str | None = None


def load_scenarios(path: Path = SCENARIOS_PATH) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["scenarios"]


def hard_violations(result: RecommendationResult, profile: PreferenceProfile,
                    rows: dict[str, CandidateRow]) -> list[str]:
    """Rule breaches judged from stored data. ``profile`` is the *effective* request (after intent merge)."""
    out: list[str] = []
    visitor = profile.visitor
    when = profile.client_datetime or datetime.now().astimezone()
    for item in result.recommendations:
        row = rows.get(item.poi_id)
        tag = item.name
        if row is None:
            out.append(f"{tag}: recommended place is not among the retrieved candidates")
            continue
        if row.distance_m > profile.radius_m + 1:
            out.append(f"{tag}: {row.distance_m / 1000:.1f} km is beyond max distance {profile.max_distance_km} km")
        if hours_for_day(row.opening_hours, when)[1]:
            out.append(f"{tag}: closed on the visit day")
        cats = set(row.categories or [row.category])
        if bad := cats & set(visitor.avoid_categories):
            out.append(f"{tag}: has avoided category {sorted(bad)}")
        facts = facts_by_key(row.facts)
        fee = facts.get("entry_fee")
        if fee and fee.verified and visitor.max_entry_fee_inr is not None:
            price = entry_fee_for(fee.value, visitor.foreign_national)
            if price is not None and price > visitor.max_entry_fee_inr:
                out.append(f"{tag}: verified fee ₹{price} exceeds cap ₹{visitor.max_entry_fee_inr}")
        wheel = facts.get("wheelchair_access")
        if visitor.wheelchair_user and wheel and wheel.verified and wheel.value.get("accessible") is False:
            out.append(f"{tag}: verified not wheelchair accessible")
        entry = facts.get("non_hindu_entry")
        if visitor.non_hindu_visitor and entry and entry.verified and entry.value.get("allowed") is False:
            if not entry.value.get("viewing_platform"):
                out.append(f"{tag}: verified no entry for non-Hindus")
            elif not any(n.kind == "entry" for n in item.notices):
                out.append(f"{tag}: entry restriction shown without its notice")
        known = {f.id for f in item.place_facts}
        if unknown := [f.id for f in item.cited_facts if f.id not in known]:
            out.append(f"{tag}: cites unknown fact ids {unknown}")
    it = result.itinerary
    if it and it.stops:
        used = (it.stops[-1].depart - it.start).total_seconds() / 3600
        if used > profile.available_hours + 1e-6:
            out.append(f"itinerary uses {used:.2f} h of {profile.available_hours} h")
    return out


def check_expectations(result: RecommendationResult, expect: dict) -> list[str]:
    names = [i.name for i in result.recommendations]
    failed: list[str] = []
    if (want := expect.get("include_any")) and not set(want) & set(names):
        failed.append(f"none of {want} recommended")
    for name in expect.get("exclude", []):
        if name in names:
            failed.append(f"{name} should not be recommended")
    for name, kind in expect.get("notice_if_present", {}).items():
        item = next((i for i in result.recommendations if i.name == name), None)
        if item and not any(n.kind == kind for n in item.notices):
            failed.append(f"{name} lacks a '{kind}' notice")
    for cat in expect.get("categories_none", []):
        if bad := [i.name for i in result.recommendations if cat in (i.categories or [i.category])]:
            failed.append(f"category '{cat}' present: {bad}")
    for label in expect.get("understood_any", []):
        if not any(label in u for u in result.understood):
            failed.append(f"did not understand '{label}' (got {result.understood})")
    if (want := expect.get("event_any")) and not set(want) & {e.name for e in result.events}:
        failed.append(f"none of events {want} surfaced")
    if (want := expect.get("season")) and not (result.season or "").startswith(want):
        failed.append(f"season {result.season!r} != {want!r}")
    if (n := expect.get("min_results")) is not None and len(names) < n:
        failed.append(f"{len(names)} results < {n}")
    if (n := expect.get("max_results")) is not None and len(names) > n:
        failed.append(f"{len(names)} results > {n}")
    if (h := expect.get("itinerary_within_hours")) is not None:
        it = result.itinerary
        if it and it.stops and (it.stops[-1].depart - it.start).total_seconds() / 3600 > h + 1e-6:
            failed.append(f"itinerary longer than {h} h")
    return failed


async def run_scenarios(engine: RecommendationEngine, candidate_source, scenarios: list[dict]) -> list[ScenarioResult]:
    results: list[ScenarioResult] = []
    for sc in scenarios:
        profile = PreferenceProfile.model_validate(sc["profile"])
        res = ScenarioResult(sc["id"], sc.get("description", ""), passed=False)
        try:
            result = await engine.recommend(profile)
        except Exception as exc:  # a crash is a failed scenario, not a crashed harness
            res.error = f"{type(exc).__name__}: {exc}"
            results.append(res)
            continue
        effective = result.request.model_copy(update={"client_datetime": profile.client_datetime})
        if any("hours available" in u for u in result.understood):
            hours = next(float(u.split()[0]) for u in result.understood if u.endswith("hours available"))
            effective = effective.model_copy(update={"available_hours": hours})
        rows = {r.poi_id: r for r in candidate_source.find_candidates(
            profile.latitude, profile.longitude, profile.radius_m, 1000)}
        res.violations = hard_violations(result, effective, rows)
        res.failed_expectations = check_expectations(result, sc.get("expect", {}))
        res.recommended = [i.name for i in result.recommendations]
        res.groundable = sum(1 for i in result.recommendations if i.place_facts)
        res.grounded = sum(1 for i in result.recommendations if i.place_facts and i.cited_facts)
        res.latency_ms = float(result.timings_ms.get("total", 0.0))
        res.generation_method = result.generation_method
        res.passed = not res.violations and not res.failed_expectations
        results.append(res)
    return results


def summarize(results: list[ScenarioResult]) -> dict:
    lat = sorted(r.latency_ms for r in results if r.error is None)
    groundable = sum(r.groundable for r in results)
    return {
        "scenarios": len(results),
        "passed": sum(r.passed for r in results),
        "pass_rate": round(sum(r.passed for r in results) / len(results), 3) if results else 0.0,
        "hard_violations": sum(len(r.violations) for r in results),
        "errors": sum(r.error is not None for r in results),
        "grounding_rate": round(sum(r.grounded for r in results) / groundable, 3) if groundable else None,
        "latency_ms_p50": round(statistics.median(lat), 1) if lat else None,
        "latency_ms_p95": round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 1) if lat else None,
        "generation_methods": sorted({r.generation_method for r in results if r.generation_method}),
    }


def write_report(results: list[ScenarioResult], summary: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps({"summary": summary, "results": [asdict(r) for r in results]},
                                                     indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["# Evaluation report", "", "| metric | value |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in summary.items()]
    lines += ["", "| scenario | result | recommended | problems |", "|---|---|---|---|"]
    for r in results:
        problems = "; ".join(r.violations + r.failed_expectations + ([r.error] if r.error else [])) or "—"
        lines.append(f"| {r.id} | {'PASS' if r.passed else 'FAIL'} | {', '.join(r.recommended[:5]) or '—'} | "
                     f"{problems.replace('|', '/')} |")
    path = out_dir / "report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
