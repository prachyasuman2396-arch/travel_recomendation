import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CloudSun, List, Map as MapIcon } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { DestinationCard } from "@/components/DestinationCard";
import { EventsCallout, ItineraryCard, RefineBox, SeasonAirLine, UnderstoodChips } from "@/components/IndiaContext";
import { CardListSkeleton, EmptyState, ErrorState, MockDataNotice } from "@/components/States";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/primitives";
import { useSearch } from "@/context/RecommendationContext";
import { useRecommendations } from "@/hooks/useRecommendations";
import { CATEGORIES } from "@/lib/categories";
import { api } from "@/services/api";
import type { RecommendationResult } from "@/types/api";

export function GenerationLabel({ result }: { result: RecommendationResult }) {
  if (result.generation_method === "mock_llm") return <Badge tone="warn">Offline demo ranking (no AI model)</Badge>;
  if (result.generation_method === "deterministic_fallback") return <Badge tone="warn">Ranked by score — AI unavailable</Badge>;
  if (result.generation_method === "llm" && result.provider) {
    const name = result.provider === "gemini" ? "Gemini" : result.provider === "groq" ? "Groq" : result.provider;
    return <Badge tone="info">Explained by {name}{result.fallback_used ? " (backup model)" : ""}</Badge>;
  }
  return null;
}

export function WeatherLine({ result }: { result: RecommendationResult }) {
  const w = result.weather;
  if (!w) {
    return (
      <p className="flex items-center gap-2 text-sm text-ink-soft">
        <CloudSun className="size-4" aria-hidden />
        {result.weather_status === "disabled" ? "Weather isn't enabled, so it wasn't considered." :
          "Weather is unavailable right now, so it wasn't considered."}
      </p>
    );
  }
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
      <CloudSun className="size-4 text-monsoon" aria-hidden />
      <span className="font-semibold">{w.condition ?? "Current weather"}</span>
      {w.temperature_c !== null && <span className="num">{Math.round(w.temperature_c)}°C</span>}
      {w.max_precipitation_probability !== null && (
        <span className="num text-ink-soft">up to {w.max_precipitation_probability}% chance of rain in your window</span>
      )}
      {w.is_mock && <Badge tone="warn">Sample weather</Badge>}
    </p>
  );
}

const SKIP_REASONS: Record<string, string> = {
  entry_restricted: "skipped because entry is restricted for you (per a cited source)",
  over_budget: "over your entry-fee limit",
  not_wheelchair_accessible: "confirmed not wheelchair accessible",
  avoided_category: "in categories you asked to avoid",
};

export function ResultsPage() {
  const { search, setSearch } = useSearch();
  const query = useRecommendations(search?.profile);
  const queryClient = useQueryClient();
  const [changes, setChanges] = useState<string[] | null>(null);
  const refine = useMutation({
    mutationFn: (message: string) => api.refine(search!.profile, message),
    onSuccess: (data) => {
      // Seed the cache for the new profile so the page swaps without a second model call.
      queryClient.setQueryData(["recommendations", data.profile], data.result);
      setChanges(data.changes);
      setSearch({ profile: data.profile, locationLabel: search!.locationLabel });
    },
  });

  if (!search) {
    return (
      <div className="mx-auto max-w-3xl px-5 py-12">
        <EmptyState title="Start by telling us where you are"
          action={<Button asChild><Link to="/explore">Plan a trip</Link></Button>}>
          Your recommendations appear here after you choose a location and what you enjoy.
        </EmptyState>
      </div>
    );
  }

  const p = search.profile;
  return (
    <div className="mx-auto max-w-5xl px-5 py-10">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-4xl font-extrabold">Your best matches</h1>
          <p className="mt-2 text-ink-soft">
            Within <span className="num">{p.max_distance_km} km</span> of {search.locationLabel}, for about{" "}
            <span className="num">{p.available_hours} h</span>
            {p.interests.length > 0 && <> — {p.interests.map((i) => CATEGORIES[i].label).join(", ")}</>}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" size="sm" asChild aria-current="page"><Link to="/results"><List /> List</Link></Button>
          <Button variant="ghost" size="sm" asChild><Link to="/map"><MapIcon /> Map</Link></Button>
          <Button variant="ghost" size="sm" asChild><Link to="/explore">Change preferences</Link></Button>
        </div>
      </div>

      <div className="mt-8 space-y-5">
        {query.isPending && (
          <>
            <p className="text-ink-soft" role="status">Finding places near you and checking the weather…</p>
            <CardListSkeleton />
          </>
        )}
        {query.isError && (
          <ErrorState title="We couldn't build your recommendations" error={query.error} onRetry={() => query.refetch()} />
        )}
        {query.data && (
          <>
            <RefineBox onRefine={(m) => refine.mutate(m)} pending={refine.isPending} changes={changes} />
            {refine.isError && (
              <ErrorState title="Couldn't apply that change" error={refine.error} onRetry={() => refine.reset()} />
            )}
            <Results result={query.data} />
          </>
        )}
      </div>
    </div>
  );
}

function Results({ result }: { result: RecommendationResult }) {
  if (result.recommendations.length === 0) {
    return (
      <EmptyState title="Nothing matched within your distance"
        action={<Button asChild variant="secondary"><Link to="/explore">Widen your search</Link></Button>}>
        {result.candidates_found === 0
          ? "We don't have destinations in this area yet. Try a larger distance or another starting point."
          : "Every nearby place was excluded (for example, closed today). Try a larger distance or more time."}
      </EmptyState>
    );
  }
  const closed = result.excluded.closed_today ?? 0;
  const ruleSkips = Object.entries(result.excluded).filter(([k, v]) => v > 0 && !["closed_today", "beyond_max_distance"].includes(k));
  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="space-y-1">
          <WeatherLine result={result} />
          <SeasonAirLine result={result} />
        </div>
        <GenerationLabel result={result} />
      </div>
      <UnderstoodChips result={result} />
      <MockDataNotice origin={result.data_origin} />
      <EventsCallout events={result.events} />
      {result.summary && <p className="max-w-[70ch] text-lg leading-relaxed">{result.summary}</p>}
      <ItineraryCard itinerary={result.itinerary} />
      <ol className="space-y-5" aria-label="Ranked recommendations">
        {result.recommendations.map((item) => (
          <li key={item.poi_id}><DestinationCard item={item} /></li>
        ))}
      </ol>
      <div className="space-y-1 text-sm text-ink-soft">
        <p className="num">
          Considered {result.candidates_found} places nearby
          {closed > 0 && `; ${closed} skipped because they're closed today`}
          {ruleSkips.map(([k, v]) => `; ${v} ${SKIP_REASONS[k] ?? k.replace(/_/g, " ")}`).join("")}.
        </p>
        <p>{result.disclaimer}</p>
      </div>
    </>
  );
}
