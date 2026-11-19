import { Check, Clock, MessageSquareText, Star, TriangleAlert } from "lucide-react";
import { Link } from "react-router";
import { DestinationImage } from "@/components/DestinationImage";
import { CitedFacts, NoticeList } from "@/components/IndiaContext";
import { Badge, Card } from "@/components/ui/primitives";
import { categoryMeta } from "@/lib/categories";
import { formatCount, formatKm } from "@/lib/utils";
import type { RecommendationItem, WeatherSuitability } from "@/types/api";

const WEATHER: Record<WeatherSuitability, { label: string; tone: "good" | "warn" | "bad" | "neutral" }> = {
  suitable: { label: "Good weather for it", tone: "good" },
  acceptable: { label: "Weather is OK", tone: "warn" },
  unsuitable: { label: "Weather not ideal", tone: "bad" },
  unknown: { label: "Weather unknown", tone: "neutral" },
};

const TIME_FIT = { fits: null, tight: "Tight for your time", unlikely: "Probably too far for your time" } as const;

export function DestinationCard({ item }: { item: RecommendationItem }) {
  const meta = categoryMeta(item.category);
  const weather = WEATHER[item.weather_suitability];
  const f = item.facts;
  const timeNote = TIME_FIT[f.time_fit];
  return (
    <Card className="grid gap-5 p-4 sm:p-5 md:grid-cols-[minmax(180px,240px)_1fr]" aria-labelledby={`dest-${item.poi_id}`}>
      <div className="relative h-44 md:h-full">
        <DestinationImage url={item.photo_url ?? item.image_url} category={item.category} name={item.name} />
        <span
          className="num absolute left-3 top-3 grid size-10 place-items-center rounded-full bg-turmeric text-lg font-extrabold text-ink shadow"
          aria-label={`Rank ${item.rank}`}
        >
          {item.rank}
        </span>
      </div>

      <div className="flex min-w-0 flex-col gap-3">
        <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
          <h3 id={`dest-${item.poi_id}`} className="text-xl font-bold leading-tight">
            {item.name}
            {item.local_names && item.local_names.length > 0 && (
              <span className="ml-2 text-base font-medium text-ink-soft">{item.local_names[0]}</span>
            )}
          </h3>
          <p className="num text-sm font-semibold text-ink-soft">{formatKm(f.distance_km)} away</p>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Badge>{meta.label}</Badge>
          {f.rating !== null ? (
            <span className="num inline-flex items-center gap-1 font-semibold">
              <Star className="size-4 fill-turmeric text-turmeric" aria-hidden />
              {f.rating.toFixed(1)}
              <span className="font-normal text-ink-soft">({formatCount(f.rating_count)} ratings)</span>
            </span>
          ) : (
            <span className="text-ink-soft">No ratings yet</span>
          )}
          <Badge tone={weather.tone}>{weather.label}</Badge>
          {timeNote && (
            <Badge tone="warn">
              <Clock className="size-3" aria-hidden /> {timeNote}
            </Badge>
          )}
        </div>

        <NoticeList notices={item.notices} compact />

        <p className="leading-relaxed">{item.reason}</p>
        {item.season_note && <p className="text-sm text-ink-soft">{item.season_note}</p>}

        {(item.advantages.length > 0 || item.considerations.length > 0) && (
          <div className="grid gap-4 text-sm sm:grid-cols-2">
            {item.advantages.length > 0 && (
              <ul className="space-y-1.5" aria-label="Good to know">
                {item.advantages.map((a) => (
                  <li key={a} className="flex gap-2">
                    <Check className="mt-0.5 size-4 shrink-0 text-sal" aria-hidden /> {a}
                  </li>
                ))}
              </ul>
            )}
            {item.considerations.length > 0 && (
              <ul className="space-y-1.5" aria-label="Keep in mind">
                {item.considerations.map((c) => (
                  <li key={c} className="flex gap-2 text-ink-soft">
                    <TriangleAlert className="mt-0.5 size-4 shrink-0 text-[#b07a10]" aria-hidden /> {c}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {item.review_insights.length > 0 && (
          <div className="flex gap-2 rounded-[var(--radius-control)] bg-paper p-3 text-sm">
            <MessageSquareText className="mt-0.5 size-4 shrink-0 text-monsoon" aria-hidden />
            <p>{item.review_insights.join(" ")}</p>
          </div>
        )}

        <CitedFacts facts={item.cited_facts} />

        <div className="mt-auto flex flex-wrap items-center gap-x-4 gap-y-1 pt-1 text-sm">
          <Link to={`/destinations/${item.poi_id}`} className="font-semibold text-monsoon underline-offset-4 hover:underline">
            View details
          </Link>
          {f.hours_today && <span className="text-ink-soft">Today: {f.hours_today}</span>}
          {!item.photo_url && item.image_url && item.image_attribution && (
            <span className="text-xs text-ink-soft">Image: {item.image_attribution}</span>
          )}
        </div>
      </div>
    </Card>
  );
}
