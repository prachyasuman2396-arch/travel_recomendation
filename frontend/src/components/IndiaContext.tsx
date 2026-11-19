/** India-specific result pieces: entry/crowd notices, sourced facts, festival calendar, itinerary, refine box. */
import { CalendarDays, Footprints, Info, Landmark, Send, ShieldAlert, TriangleAlert, Wind } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Badge, Card, Input } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";
import type { AirQuality, Fact, FestivalEvent, Itinerary, Notice, RecommendationResult } from "@/types/api";

const SEVERITY_ORDER = { critical: 0, warning: 1, info: 2 } as const;

const NOTICE_STYLE = {
  critical: { box: "bg-[#f7e3df] ring-[#e2b3aa]", icon: ShieldAlert, iconClass: "text-danger" },
  warning: { box: "bg-[#f8eed6] ring-[#e8d29c]", icon: TriangleAlert, iconClass: "text-[#8a5d0c]" },
  info: { box: "bg-paper ring-line", icon: Info, iconClass: "text-monsoon" },
} as const;

function SourceLink({ url, label = "source" }: { url: string | null | undefined; label?: string }) {
  if (!url) return null;
  return (
    <a href={url} target="_blank" rel="noreferrer" className="whitespace-nowrap text-monsoon underline underline-offset-2">
      {label}
    </a>
  );
}

/** Rules computed by code from sourced facts. Shown before the AI text so they can't be missed. */
export function NoticeList({ notices, compact = false }: { notices: Notice[] | undefined; compact?: boolean }) {
  if (!notices?.length) return null;
  const sorted = [...notices].sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]);
  const shown = compact ? sorted.filter((n) => n.severity !== "info") : sorted;
  const infos = compact ? sorted.filter((n) => n.severity === "info") : [];
  return (
    <div className="space-y-2" aria-label="Before you go">
      {shown.map((n, i) => {
        const style = NOTICE_STYLE[n.severity];
        const Icon = style.icon;
        return (
          <p key={`${n.kind}-${i}`} className={cn("flex gap-2.5 rounded-[var(--radius-control)] p-3 text-sm ring-1", style.box)}>
            <Icon className={cn("mt-0.5 size-4 shrink-0", style.iconClass)} aria-hidden />
            <span>
              {n.text}{" "}
              {n.verified ? <SourceLink url={n.source_url} /> : <span className="text-ink-soft">(reported, not verified)</span>}
            </span>
          </p>
        );
      })}
      {infos.length > 0 && (
        <ul className="flex flex-wrap gap-2">
          {infos.map((n, i) => (
            <li key={`${n.kind}-info-${i}`}>
              <Badge tone="neutral" title={n.verified ? "From a cited source" : "Reported, not verified"}>
                {n.text}{!n.verified && " · reported"}
              </Badge>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Facts the explanation relied on, each with its source. */
export function CitedFacts({ facts }: { facts: Fact[] | undefined }) {
  if (!facts?.length) return null;
  return (
    <details className="group text-sm">
      <summary className="cursor-pointer select-none font-semibold text-monsoon">
        Based on {facts.length} sourced {facts.length === 1 ? "fact" : "facts"}
      </summary>
      <ul className="mt-2 space-y-1.5 border-l-2 border-line pl-3">
        {facts.map((f) => (
          <li key={f.id}>
            {f.text}{" "}
            <span className="text-ink-soft">
              — {f.source_name ?? "source"}
              {f.verified ? "" : " (unverified)"} <SourceLink url={f.source_url} label="open" />
            </span>
          </li>
        ))}
      </ul>
    </details>
  );
}

const AQI_TONE: Record<string, "good" | "warn" | "bad" | "neutral"> = {
  good: "good", moderate: "warn", unhealthy_sensitive: "bad", unhealthy: "bad", very_unhealthy: "bad",
  hazardous: "bad",
};

function aqiLabel(aq: AirQuality): string {
  const names: Record<string, string> = {
    good: "good", moderate: "moderate", unhealthy_sensitive: "unhealthy for sensitive groups",
    unhealthy: "unhealthy", very_unhealthy: "very unhealthy", hazardous: "hazardous",
  };
  return names[aq.category ?? ""] ?? "unknown";
}

/** "Post-monsoon · AQI 112 (unhealthy for sensitive groups)" */
export function SeasonAirLine({ result }: { result: RecommendationResult }) {
  const aq = result.air_quality;
  if (!result.season && !aq) return null;
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
      {result.season && (
        <span className="inline-flex items-center gap-1.5">
          <CalendarDays className="size-4 text-sal" aria-hidden /> {result.season}
        </span>
      )}
      {aq?.us_aqi != null && (
        <span className="inline-flex items-center gap-1.5">
          <Wind className="size-4 text-ink-soft" aria-hidden />
          <span className="num">AQI {Math.round(aq.us_aqi)}</span>
          <Badge tone={AQI_TONE[aq.category ?? ""] ?? "neutral"}>{aqiLabel(aq)}</Badge>
        </span>
      )}
    </p>
  );
}

/** What the free-text request was understood as. Lets people catch a misread before trusting results. */
export function UnderstoodChips({ result }: { result: RecommendationResult }) {
  if (!result.understood?.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <span className="text-ink-soft">We understood:</span>
      {result.understood.map((u) => <Badge key={u} tone="info">{u}</Badge>)}
    </div>
  );
}

const fmtDate = (iso: string) =>
  new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" });

export function EventsCallout({ events }: { events: FestivalEvent[] | undefined }) {
  if (!events?.length) return null;
  return (
    <Card className="space-y-2 bg-[#fbf3e1] p-4 ring-[#ecd7a6]">
      <h2 className="flex items-center gap-2 font-bold">
        <Landmark className="size-4 text-[#8a5d0c]" aria-hidden /> Festivals around your visit
      </h2>
      <ul className="space-y-2 text-sm">
        {events.map((e) => (
          <li key={e.name + e.start}>
            <span className="font-semibold">{e.name}</span>{" "}
            <span className="num text-ink-soft">
              {fmtDate(e.start)}{fmtDate(e.end) !== fmtDate(e.start) && `–${fmtDate(e.end)}`}, {e.location_name}
            </span>
            <span className="block">{e.description} <SourceLink url={e.source_url} /></span>
            {e.date_note && <span className="block text-xs text-ink-soft">{e.date_note}</span>}
          </li>
        ))}
      </ul>
    </Card>
  );
}

const fmtTime = (iso: string) =>
  new Date(iso).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit", timeZone: "Asia/Kolkata" });

export function ItineraryCard({ itinerary }: { itinerary: Itinerary | null | undefined }) {
  if (!itinerary) return null;
  if (itinerary.stops.length === 0) {
    return itinerary.notes.length ? <p className="text-sm text-ink-soft">{itinerary.notes.join(" ")}</p> : null;
  }
  return (
    <Card className="p-5" aria-labelledby="itinerary-h">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="itinerary-h" className="flex items-center gap-2 text-lg font-bold">
          <Footprints className="size-5 text-sal" aria-hidden /> A plan that fits your time
        </h2>
        <p className="num text-sm text-ink-soft">
          {fmtTime(itinerary.start)}–{fmtTime(itinerary.end)} · {itinerary.total_travel_minutes} min travelling
          {itinerary.travel_method === "estimate" && " (estimated)"}
        </p>
      </div>
      <ol className="mt-4 space-y-0">
        {itinerary.stops.map((s, i) => (
          <li key={s.poi_id} className="grid grid-cols-[4.5rem_1fr] gap-3">
            <span className="num pt-0.5 text-sm font-semibold">{fmtTime(s.arrive)}</span>
            <div className={cn("relative border-l-2 border-sal/40 pb-4 pl-4", i === itinerary.stops.length - 1 && "pb-0")}>
              <span className="absolute -left-[7px] top-1.5 size-3 rounded-full bg-sal ring-2 ring-white" aria-hidden />
              <p className="font-semibold">{s.name}</p>
              <p className="num text-sm text-ink-soft">
                {s.visit_minutes} min visit
                {s.travel_minutes > 0 && ` · ${s.travel_minutes} min to get there`}
                {s.wait_minutes > 0 && ` · waits ${s.wait_minutes} min for opening`}
                {!s.hours_known && " · hours unknown"}
              </p>
            </div>
          </li>
        ))}
      </ol>
      {(itinerary.notes.length > 0 || itinerary.skipped.length > 0) && (
        <div className="mt-3 space-y-1 text-xs text-ink-soft">
          {itinerary.notes.map((n) => <p key={n}>{n}</p>)}
          {itinerary.skipped.length > 0 && <p>Didn't fit: {itinerary.skipped.join(", ")}.</p>}
        </div>
      )}
    </Card>
  );
}

const REFINE_SUGGESTIONS = ["Fewer temples", "Only 2 hours", "Kuch khana bhi", "Avoid crowds"];

/** Conversational refinement: "fewer temples", "sirf do ghante". */
export function RefineBox({ onRefine, pending, changes }: {
  onRefine: (message: string) => void;
  pending: boolean;
  changes: string[] | null;
}) {
  const [message, setMessage] = useState("");
  function submit(e: FormEvent) {
    e.preventDefault();
    const m = message.trim();
    if (m) onRefine(m);
  }
  return (
    <Card className="space-y-3 p-4">
      <form className="flex gap-2" onSubmit={submit}>
        <label htmlFor="refine" className="sr-only">Change these results</label>
        <Input id="refine" value={message} onChange={(e) => setMessage(e.target.value)} maxLength={300}
          placeholder="Change something: fewer temples, only 2 hours, add food…" />
        <Button type="submit" disabled={pending || !message.trim()}>
          <Send aria-hidden /> {pending ? "Updating…" : "Update"}
        </Button>
      </form>
      <div className="flex flex-wrap gap-2">
        {REFINE_SUGGESTIONS.map((s) => (
          <button key={s} type="button" disabled={pending} onClick={() => { setMessage(s); onRefine(s); }}
            className="rounded-full bg-mist px-3 py-1 text-xs font-medium hover:bg-line disabled:opacity-50">
            {s}
          </button>
        ))}
      </div>
      {changes && (
        <p className="text-sm" role="status">
          <span className="text-ink-soft">Updated: </span>{changes.join(", ")}
        </p>
      )}
    </Card>
  );
}
