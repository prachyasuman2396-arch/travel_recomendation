import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { DestinationImage } from "@/components/DestinationImage";
import { HeroContours } from "@/components/HeroContours";
import { LocationPicker } from "@/components/LocationPicker";
import { ErrorState } from "@/components/States";
import { Button } from "@/components/ui/button";
import { Badge, Card, Skeleton } from "@/components/ui/primitives";
import { CATEGORIES, FEATURED_INTERESTS, categoryMeta } from "@/lib/categories";
import { api } from "@/services/api";
import type { Place } from "@/services/geocode";

const STEPS = [
  { title: "Set your starting point", body: "Share your location or search for a town. Tell us how far you'll go and how long you have." },
  { title: "Say what you enjoy, and who's coming", body: "Describe the day in English or Hinglish — \"purane mandir, bheed kam\". Tell us if you're not Hindu, visiting from abroad or travelling with elders." },
  { title: "Get a plan you can trust", body: "Places are checked against opening hours, festival crowds, the season and air quality, then ranked, explained with cited sources and fitted into a timed route." },
];

export function LandingPage() {
  const navigate = useNavigate();
  const [place, setPlace] = useState<Place | null>(null);
  const featured = useQuery({ queryKey: ["pois", "featured"], queryFn: () => api.pois({ limit: 6 }) });

  function explore() {
    const params = new URLSearchParams();
    if (place) {
      params.set("lat", String(place.latitude));
      params.set("lon", String(place.longitude));
      params.set("label", place.label);
    }
    navigate(`/explore${params.size ? `?${params}` : ""}`);
  }

  return (
    <>
      <section className="border-b border-line bg-white">
        <div className="mx-auto grid max-w-6xl items-center gap-10 px-5 py-14 md:grid-cols-[1.05fr_1fr] md:py-20">
          <div className="space-y-7">
            <h1 className="text-[2.6rem] font-extrabold leading-[1.02] sm:text-6xl">
              Temples, caves and lakes that fit your day.
            </h1>
            <p className="max-w-[46ch] text-lg leading-relaxed text-ink-soft">
              Built for travelling in India. We know which shrines admit non-Hindus, what foreigners pay at the
              gate and when a festival will fill the streets — and we tell you where each fact comes from.
            </p>
            <div className="space-y-4 rounded-[var(--radius-card)] bg-paper p-4 ring-1 ring-line">
              <LocationPicker value={place} onChange={setPlace} />
              <Button size="lg" className="w-full sm:w-auto" onClick={explore}>
                Explore places
              </Button>
            </div>
          </div>
          <div className="aspect-[5/4] w-full">
            <HeroContours />
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-5 pt-16" aria-labelledby="categories-heading">
        <h2 id="categories-heading" className="text-3xl font-bold">What are you in the mood for?</h2>
        <ul className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
          {FEATURED_INTERESTS.map((key) => {
            const c = CATEGORIES[key];
            const Icon = c.icon;
            return (
              <li key={key}>
                <Link
                  to={`/explore?interest=${key}`}
                  className="group flex h-full flex-col gap-2 rounded-[var(--radius-card)] bg-white p-4 ring-1 ring-line transition-colors hover:ring-sal"
                >
                  <Icon className="size-6" style={{ color: c.tint }} aria-hidden />
                  <span className="font-bold">{c.label}</span>
                  <span className="text-sm text-ink-soft">{c.blurb}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </section>

      <section className="mx-auto max-w-6xl px-5 pt-16" aria-labelledby="featured-heading">
        <div className="flex items-end justify-between gap-4">
          <h2 id="featured-heading" className="text-3xl font-bold">Places in our guide</h2>
          {featured.data && featured.data.total > 0 && (
            <span className="num text-sm text-ink-soft">{featured.data.total} destinations</span>
          )}
        </div>
        <div className="mt-6">
          {featured.isPending && (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {Array.from({ length: 3 }, (_, i) => <Skeleton key={i} className="h-56 rounded-[var(--radius-card)]" />)}
            </div>
          )}
          {featured.isError && (
            <ErrorState title="Couldn't load destinations" error={featured.error} onRetry={() => featured.refetch()} />
          )}
          {featured.data && featured.data.items.length === 0 && (
            <Card className="p-6 text-ink-soft">
              No destinations have been collected yet. An administrator can load data from the data management page.
            </Card>
          )}
          {featured.data && featured.data.items.length > 0 && (
            <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {featured.data.items.map((p) => (
                <li key={p.poi_id}>
                  <Link to={`/destinations/${p.poi_id}`} className="block h-full">
                    <Card className="flex h-full flex-col gap-3 p-3 transition-shadow hover:shadow-md">
                      <div className="h-36">
                        <DestinationImage url={null} category={p.category} name={p.name} />
                      </div>
                      <div className="space-y-1 px-1 pb-1">
                        <p className="font-bold leading-snug">{p.name}</p>
                        <p className="text-sm text-ink-soft">{categoryMeta(p.category).label}</p>
                        {p.data_origin === "mock" && <Badge tone="warn">Sample data</Badge>}
                      </div>
                    </Card>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-5 pt-16" aria-labelledby="how-heading">
        <h2 id="how-heading" className="text-3xl font-bold">How it works</h2>
        <ol className="mt-6 grid gap-6 md:grid-cols-3">
          {STEPS.map((s, i) => (
            <li key={s.title} className="flex gap-4">
              <span className="num grid size-10 shrink-0 place-items-center rounded-full bg-sal font-bold text-white">
                {i + 1}
              </span>
              <div>
                <h3 className="font-bold">{s.title}</h3>
                <p className="mt-1 text-ink-soft">{s.body}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>
    </>
  );
}
