import { List, Map as MapIcon } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { RadiusMap } from "@/components/RadiusMap";
import { EmptyState, ErrorState, MockDataNotice } from "@/components/States";
import { Button } from "@/components/ui/button";
import { Card, Skeleton } from "@/components/ui/primitives";
import { useSearch } from "@/context/RecommendationContext";
import { useRecommendations } from "@/hooks/useRecommendations";
import { categoryMeta } from "@/lib/categories";
import { cn, formatKm } from "@/lib/utils";
import { GenerationLabel } from "@/pages/ResultsPage";

export function MapPage() {
  const { search } = useSearch();
  const query = useRecommendations(search?.profile);
  const [selected, setSelected] = useState<string | null>(null);

  if (!search) {
    return (
      <div className="mx-auto max-w-3xl px-5 py-12">
        <EmptyState title="Nothing to map yet" action={<Button asChild><Link to="/explore">Plan a trip</Link></Button>}>
          Choose a starting point and interests to see recommended places on the map.
        </EmptyState>
      </div>
    );
  }
  const center: [number, number] = [search.profile.latitude, search.profile.longitude];
  const data = query.data;

  return (
    <div className="mx-auto max-w-6xl px-5 py-10">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h1 className="text-4xl font-extrabold">On the map</h1>
        <div className="flex gap-2">
          <Button variant="ghost" size="sm" asChild><Link to="/results"><List /> List</Link></Button>
          <Button variant="secondary" size="sm" asChild aria-current="page"><Link to="/map"><MapIcon /> Map</Link></Button>
        </div>
      </div>
      <p className="mt-2 text-sm text-ink-soft">
        Numbered pins are your recommendations; small grey dots are other places we considered. Distances are
        straight-line, not travel routes.
      </p>

      <div className="mt-6 space-y-4">
        {query.isPending && <Skeleton className="h-[480px] w-full rounded-[var(--radius-card)]" />}
        {query.isError && <ErrorState title="We couldn't load the map data" error={query.error} onRetry={() => query.refetch()} />}
        {data && (
          <>
            <div className="flex justify-end"><GenerationLabel result={data} /></div>
            <MockDataNotice origin={data.data_origin} />
            <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
              <RadiusMap
                center={center}
                radiusKm={search.profile.max_distance_km}
                selectedId={selected}
                onSelect={setSelected}
                recommended={data.recommendations.map((r) => ({
                  id: r.poi_id, name: r.name, latitude: r.latitude, longitude: r.longitude, rank: r.rank,
                  distanceKm: r.facts.distance_km, subtitle: categoryMeta(r.category).label,
                }))}
                others={data.candidates.filter((c) => !c.recommended).map((c) => ({
                  id: c.poi_id, name: c.name, latitude: c.latitude, longitude: c.longitude, distanceKm: c.distance_km,
                }))}
              />
              <Card className="max-h-[480px] overflow-auto p-2">
                {data.recommendations.length === 0 ? (
                  <p className="p-4 text-ink-soft">No recommendations within your distance.</p>
                ) : (
                  <ol aria-label="Recommended places">
                    {data.recommendations.map((r) => (
                      <li key={r.poi_id}>
                        <button
                          type="button"
                          onClick={() => setSelected(r.poi_id)}
                          className={cn("flex w-full gap-3 rounded-[10px] p-3 text-left hover:bg-mist",
                            selected === r.poi_id && "bg-mist")}
                        >
                          <span className="num grid size-7 shrink-0 place-items-center rounded-full bg-turmeric text-sm font-bold">{r.rank}</span>
                          <span className="min-w-0">
                            <span className="block font-semibold leading-snug">{r.name}</span>
                            <span className="num text-sm text-ink-soft">{formatKm(r.facts.distance_km)}</span>
                          </span>
                        </button>
                        {selected === r.poi_id && (
                          <div className="px-3 pb-3 text-sm">
                            <p>{r.reason}</p>
                            <Link to={`/destinations/${r.poi_id}`} className="mt-1 inline-block font-semibold text-monsoon hover:underline">
                              View details
                            </Link>
                          </div>
                        )}
                      </li>
                    ))}
                  </ol>
                )}
              </Card>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
