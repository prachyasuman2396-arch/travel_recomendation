import { useQuery } from "@tanstack/react-query";
import { ExternalLink, Star } from "lucide-react";
import { Link, useParams } from "react-router";
import { DestinationImage } from "@/components/DestinationImage";
import { NoticeList } from "@/components/IndiaContext";
import { RadiusMap } from "@/components/RadiusMap";
import { ErrorState, MockDataNotice } from "@/components/States";
import { Badge, Card, Skeleton } from "@/components/ui/primitives";
import { categoryMeta } from "@/lib/categories";
import { formatCount } from "@/lib/utils";
import { ApiError, api } from "@/services/api";

const SOURCE_NAMES: Record<string, string> = { google_places: "Google", google: "Google", yelp: "Yelp" };
const FACT_KIND: Record<string, string> = {
  non_hindu_entry: "entry", foreigner_entry: "entry", entry_fee: "fee", wheelchair_access: "accessibility",
  phones_allowed: "phones", photography: "phones", dress_code: "dress", crowd_note: "crowd",
};
const ASPECT_NAMES: Record<string, string> = {
  environment: "Surroundings", cleanliness: "Cleanliness", facilities: "Facilities", food: "Food", safety: "Safety",
  accessibility: "Accessibility", visitor_experience: "Visitor experience", crowding: "Crowds",
};

export function DestinationPage() {
  const { poiId = "" } = useParams();
  const poi = useQuery({ queryKey: ["poi", poiId], queryFn: () => api.poi(poiId) });
  const reviews = useQuery({ queryKey: ["poi", poiId, "reviews"], queryFn: () => api.poiReviews(poiId),
    enabled: poi.isSuccess });
  const photos = useQuery({ queryKey: ["poi", poiId, "photos"], queryFn: () => api.poiPhotos(poiId),
    enabled: poi.isSuccess });

  if (poi.isPending) {
    return (
      <div className="mx-auto max-w-5xl space-y-4 px-5 py-10" aria-busy="true">
        <Skeleton className="h-10 w-2/3" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (poi.isError) {
    const notFound = poi.error instanceof ApiError && poi.error.status === 404;
    return (
      <div className="mx-auto max-w-3xl px-5 py-12">
        <ErrorState title={notFound ? "We don't know this place" : "Couldn't load this place"} error={poi.error}
          onRetry={notFound ? undefined : () => poi.refetch()} />
        <Link to="/results" className="mt-4 inline-block text-monsoon hover:underline">Back to results</Link>
      </div>
    );
  }

  const d = poi.data;
  const meta = categoryMeta(d.category);
  const s = d.sentiment;
  const photoList = photos.data?.items ?? [];
  const firstPhoto = photoList.find((p) => p.permitted_photo_url);
  const googlePhoto = (d.google_photo_count ?? 0) > 0 && d.data_origin === "live" ? api.googlePhotoUrl(d.poi_id) : null;
  const heroUrl = googlePhoto ?? firstPhoto?.permitted_photo_url ?? d.background?.image_url ?? null;
  const facts = (d.facts ?? []).filter((f) => f.status !== "rejected");
  // One line per fact key: a verified fact hides unverified duplicates of the same key.
  const factLines = facts.filter((f, i) => facts.findIndex((g) => g.fact_key === f.fact_key) === i);

  return (
    <article className="mx-auto max-w-5xl px-5 py-10">
      <Link to="/results" className="text-sm text-monsoon hover:underline">Back to results</Link>
      <header className="mt-3 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-4xl font-extrabold">{d.name}</h1>
          {d.alt_names && d.alt_names.length > 0 && (
            <p className="mt-1 text-lg text-ink-soft">{d.alt_names.slice(0, 3).join(" · ")}</p>
          )}
          <p className="mt-2 flex flex-wrap items-center gap-2 text-ink-soft">
            <Badge>{meta.label}</Badge>
            {d.address && <span>{d.address}</span>}
          </p>
        </div>
        {d.rating !== null && (
          <p className="num flex items-center gap-1.5 text-2xl font-bold">
            <Star className="size-6 fill-turmeric text-turmeric" aria-hidden /> {d.rating.toFixed(1)}
            <span className="text-base font-normal text-ink-soft">({formatCount(d.rating_count)} ratings)</span>
          </p>
        )}
      </header>

      <div className="mt-4"><MockDataNotice origin={d.data_origin} /></div>

      <div className="mt-6 grid gap-6 lg:grid-cols-[1.4fr_1fr]">
        <div className="space-y-6">
          <div className="h-72">
            <DestinationImage url={heroUrl} category={d.category} name={d.name} />
          </div>
          {googlePhoto ? (
            <p className="-mt-4 text-xs text-ink-soft">Photo from Google Maps contributors</p>
          ) : !firstPhoto && d.background?.image_url ? (
            <p className="-mt-4 text-xs text-ink-soft">{d.background.image_attribution ?? "Image via Wikimedia Commons"}</p>
          ) : firstPhoto?.page_url && (
            <p className="-mt-4 text-xs text-ink-soft">
              Photo by {firstPhoto.owner_name ?? "a Flickr user"} on{" "}
              <a className="underline" href={firstPhoto.page_url} target="_blank" rel="noreferrer">Flickr</a>
            </p>
          )}
          {d.description && <p className="max-w-[65ch] text-lg leading-relaxed">{d.description}</p>}

          {factLines.length > 0 && (
            <section aria-labelledby="before-h" className="space-y-3">
              <h2 id="before-h" className="text-2xl font-bold">Before you go</h2>
              <NoticeList notices={factLines.map((f) => ({
                kind: FACT_KIND[f.fact_key] ?? f.fact_key,
                text: f.status === "verified" ? f.display_text : `Reported: ${f.display_text}`,
                severity: f.fact_key === "non_hindu_entry" ? ("warning" as const) : ("info" as const),
                source_url: f.source_url || null,
                verified: f.status === "verified",
              }))} />
              <p className="text-xs text-ink-soft">
                Verified facts come from a cited source checked by a person. Reported facts were extracted
                automatically or come from map data — confirm locally.
              </p>
            </section>
          )}

          {d.background?.summary && (
            <section aria-labelledby="bg-h" className="space-y-2">
              <h2 id="bg-h" className="text-2xl font-bold">Background</h2>
              <p className="max-w-[65ch] leading-relaxed">{d.background.summary}</p>
              <p className="text-xs text-ink-soft">
                From <a className="underline" href={d.background.url} target="_blank" rel="noreferrer">
                  {d.background.title ?? "Wikipedia"}</a>
                {d.background.license ? `, ${d.background.license}` : ""}
                {d.background.data_origin === "mock" && " — sample text, not the real article"}
              </p>
            </section>
          )}

          <section aria-labelledby="reviews-h" className="space-y-3">
            <h2 id="reviews-h" className="text-2xl font-bold">What visitors say</h2>
            {s?.status === "available" ? (
              <Card className="space-y-3 p-5">
                <p>
                  Based on <span className="num">{s.reviews_analyzed}</span> reviews, overall sentiment is{" "}
                  <strong>{s.overall}</strong>.
                </p>
                <ul className="flex flex-wrap gap-2">
                  {Object.entries(s.aspects).map(([name, a]) => (
                    <li key={name}>
                      <Badge tone={a.label === "positive" ? "good" : a.label === "negative" ? "bad" : "neutral"}>
                        {ASPECT_NAMES[name] ?? name}: {a.label}{" "}
                        <span className="num opacity-70">({a.mentions})</span>
                      </Badge>
                    </li>
                  ))}
                </ul>
              </Card>
            ) : (
              <p className="text-ink-soft">Not enough reviews to summarise sentiment.</p>
            )}
            {reviews.isPending && <Skeleton className="h-24 w-full" />}
            {reviews.isError && <ErrorState title="Couldn't load reviews" error={reviews.error} onRetry={() => reviews.refetch()} />}
            {reviews.data && reviews.data.items.length > 0 && (
              <ul className="space-y-3">
                {reviews.data.items.map((r) => (
                  <li key={r.review_id}>
                    <Card className="space-y-1 p-4 text-sm">
                      <p className="num flex items-center gap-2 text-ink-soft">
                        {r.rating !== null && <span className="font-semibold text-ink">{r.rating}/5</span>}
                        <span>
                          {r.author_name ? (
                            r.author_url ? (
                              <a className="underline" href={r.author_url} target="_blank" rel="noreferrer">{r.author_name}</a>
                            ) : r.author_name
                          ) : null}
                          {r.author_name ? " on " : ""}{SOURCE_NAMES[r.source] ?? r.source}
                        </span>
                        {r.review_date && <span>{new Date(r.review_date).toLocaleDateString()}</span>}
                      </p>
                      {r.review_text && <p className="leading-relaxed">{r.review_text}</p>}
                    </Card>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {photoList.length > 0 && (
            <section aria-labelledby="photos-h" className="space-y-3">
              <h2 id="photos-h" className="text-2xl font-bold">Photos taken here</h2>
              <p className="text-sm text-ink-soft">
                Geotagged Flickr photos whose tags or titles mention this place.{" "}
                <span className="num">{d.photo_count}</span> matched.
              </p>
              <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                {photoList.map((p) => (
                  <li key={p.external_photo_id} className="space-y-1">
                    <div className="h-28">
                      <DestinationImage url={p.permitted_photo_url} category={d.category} name={d.name} />
                    </div>
                    {p.page_url && (
                      <a className="block truncate text-xs text-monsoon underline" href={p.page_url} target="_blank" rel="noreferrer">
                        {p.title ?? "View on Flickr"}
                      </a>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>

        <aside className="space-y-4">
          <RadiusMap center={[d.latitude, d.longitude]} className="h-64 w-full rounded-[var(--radius-card)]"
            recommended={[{ id: d.poi_id, name: d.name, latitude: d.latitude, longitude: d.longitude, rank: 1 }]} />
          <Card className="space-y-4 p-5 text-sm">
            <div>
              <h2 className="font-bold">Location</h2>
              <p className="num text-ink-soft">{d.latitude.toFixed(5)}, {d.longitude.toFixed(5)}</p>
            </div>
            <div>
              <h2 className="font-bold">Opening hours</h2>
              {d.opening_hours?.weekdayDescriptions?.length ? (
                <ul className="mt-1 space-y-0.5 text-ink-soft">
                  {d.opening_hours.weekdayDescriptions.map((line) => <li key={line}>{line}</li>)}
                </ul>
              ) : (
                <p className="text-ink-soft">Not listed. Check locally before you go.</p>
              )}
            </div>
            {d.website && (
              <a href={d.website} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 font-semibold text-monsoon hover:underline">
                Official website <ExternalLink className="size-3.5" aria-hidden />
              </a>
            )}
            {d.ratings.length > 0 && (
              <div>
                <h2 className="font-bold">Ratings by source</h2>
                <ul className="num mt-1 text-ink-soft">
                  {d.ratings.map((r) => (
                    <li key={r.source}>{SOURCE_NAMES[r.source] ?? r.source}: {r.rating.toFixed(1)} ({formatCount(r.rating_count)})</li>
                  ))}
                </ul>
              </div>
            )}
          </Card>
        </aside>
      </div>
    </article>
  );
}
