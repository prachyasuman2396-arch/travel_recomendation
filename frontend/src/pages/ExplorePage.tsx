import { zodResolver } from "@hookform/resolvers/zod";
import { Controller, useForm } from "react-hook-form";
import { useNavigate, useSearchParams } from "react-router";
import { LocationPicker } from "@/components/LocationPicker";
import { Button } from "@/components/ui/button";
import { Card, FieldError, Input, Label, Select, Textarea } from "@/components/ui/primitives";
import { Slider } from "@/components/ui/slider";
import { useSearch } from "@/context/RecommendationContext";
import { CATEGORIES, INTERESTS } from "@/lib/categories";
import { cn } from "@/lib/utils";
import { exploreDefaults, exploreSchema, toPreferenceProfile, type ExploreValues } from "@/schemas/explore";
import type { Interest } from "@/types/api";

type VisitorKey = "nonHinduVisitor" | "foreignNational" | "wheelchairUser" | "withChildren" | "withElderly" |
  "avoidCrowds";

const VISITOR_OPTIONS: { key: VisitorKey; label: string; hint: string }[] = [
  { key: "nonHinduVisitor", label: "Not Hindu", hint: "Some temples (Lingaraj, Jagannath Puri) admit Hindus only" },
  { key: "foreignNational", label: "Foreign national", hint: "Monuments charge foreign visitors more" },
  { key: "withElderly", label: "With elderly people", hint: "Fewer steps and shorter walks" },
  { key: "withChildren", label: "With children", hint: "Favour kid-friendly places" },
  { key: "wheelchairUser", label: "Wheelchair user", hint: "Skip places confirmed inaccessible" },
  { key: "avoidCrowds", label: "Avoid crowds", hint: "Down-rank places busy with festivals" },
];

function initialValues(params: URLSearchParams): ExploreValues {
  const lat = Number(params.get("lat"));
  const lon = Number(params.get("lon"));
  const interest = params.get("interest");
  const hasCoords = params.has("lat") && params.has("lon") && Number.isFinite(lat) && Number.isFinite(lon);
  return {
    ...exploreDefaults,
    ...(hasCoords ? { latitude: lat, longitude: lon, locationLabel: params.get("label") ?? "Selected location" } : {}),
    interests: interest && interest in CATEGORIES ? [interest as Interest] : [],
  };
}

export function ExplorePage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { setSearch } = useSearch();
  const { control, register, handleSubmit, watch, setValue, formState: { errors, isSubmitting } } =
    useForm<ExploreValues>({ resolver: zodResolver(exploreSchema), defaultValues: initialValues(params) });

  const [lat, lon, label] = watch(["latitude", "longitude", "locationLabel"]);
  const place = Number.isFinite(lat) && Number.isFinite(lon) && label ? { label, latitude: lat, longitude: lon } : null;

  function onSubmit(values: ExploreValues) {
    setSearch({ profile: toPreferenceProfile(values), locationLabel: values.locationLabel });
    navigate("/results");
  }

  return (
    <div className="mx-auto max-w-3xl px-5 py-12">
      <h1 className="text-4xl font-extrabold">Plan today's outing</h1>
      <p className="mt-3 text-lg text-ink-soft">The more you tell us, the better the reasons we can give.</p>

      <form className="mt-10 space-y-8" onSubmit={handleSubmit(onSubmit)} noValidate>
        <Card className="space-y-3 p-5">
          <h2 className="text-lg font-bold">Starting point</h2>
          <LocationPicker
            value={place}
            invalid={Boolean(errors.latitude || errors.locationLabel)}
            onChange={(p) => {
              setValue("latitude", p.latitude, { shouldValidate: true });
              setValue("longitude", p.longitude, { shouldValidate: true });
              setValue("locationLabel", p.label, { shouldValidate: true });
            }}
          />
          <FieldError message={errors.locationLabel?.message ?? errors.latitude?.message} />
        </Card>

        <Card className="space-y-4 p-5">
          <h2 className="text-lg font-bold" id="interests-label">What do you enjoy?</h2>
          <Controller
            control={control}
            name="interests"
            render={({ field }) => (
              <div role="group" aria-labelledby="interests-label" className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                {INTERESTS.map((key) => {
                  const c = CATEGORIES[key];
                  const Icon = c.icon;
                  const on = field.value.includes(key);
                  return (
                    <button
                      key={key}
                      type="button"
                      aria-pressed={on}
                      onClick={() => field.onChange(on ? field.value.filter((v) => v !== key) : [...field.value, key])}
                      className={cn(
                        "flex items-center gap-2.5 rounded-[var(--radius-control)] px-3 py-2.5 text-left text-sm font-medium ring-1 transition-colors",
                        on ? "bg-sal text-white ring-sal" : "bg-white ring-line hover:bg-mist",
                      )}
                    >
                      <Icon className="size-4 shrink-0" aria-hidden /> {c.label}
                    </button>
                  );
                })}
              </div>
            )}
          />
          <FieldError message={errors.interests?.message} />
          <div className="space-y-2">
            <Label htmlFor="nl">Describe your ideal outing</Label>
            <Textarea id="nl" placeholder="Old temples and quiet places, no shopping. Sirf 4 ghante hain."
              aria-invalid={Boolean(errors.naturalLanguage) || undefined} {...register("naturalLanguage")} />
            <FieldError message={errors.naturalLanguage?.message} />
          </div>
        </Card>

        <Card className="grid gap-6 p-5 sm:grid-cols-2">
          <Controller
            control={control}
            name="maxDistanceKm"
            render={({ field }) => (
              <div className="space-y-3">
                <div className="flex justify-between">
                  <Label id="dist-label">How far will you travel?</Label>
                  <span className="num text-sm font-semibold">{field.value} km</span>
                </div>
                <Slider aria-label="Maximum distance in kilometres" min={1} max={100} step={1} value={[field.value]}
                  onValueChange={([v]) => field.onChange(v)} />
                <FieldError message={errors.maxDistanceKm?.message} />
              </div>
            )}
          />
          <Controller
            control={control}
            name="availableHours"
            render={({ field }) => (
              <div className="space-y-3">
                <div className="flex justify-between">
                  <Label id="time-label">Time available</Label>
                  <span className="num text-sm font-semibold">{field.value} h</span>
                </div>
                <Slider aria-label="Available time in hours" min={0.5} max={12} step={0.5} value={[field.value]}
                  onValueChange={([v]) => field.onChange(v)} />
                <FieldError message={errors.availableHours?.message} />
              </div>
            )}
          />
          <div className="space-y-2">
            <Label htmlFor="travel">Who's going?</Label>
            <Select id="travel" {...register("travelType", { setValueAs: (v) => v || undefined })}>
              <option value="">Not specified</option>
              <option value="solo">Just me</option>
              <option value="couple">A couple</option>
              <option value="family">Family with kids</option>
              <option value="friends">Friends</option>
              <option value="group">A larger group</option>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="budget">Budget</Label>
            <Select id="budget" {...register("budget", { setValueAs: (v) => v || undefined })}>
              <option value="">Not specified</option>
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
            </Select>
          </div>
          <div className="space-y-2 sm:col-span-2">
            <Label htmlFor="constraints">Anything else? One per line</Label>
            <Textarea id="constraints" className="min-h-20" placeholder={"No long climbs\nVegetarian food nearby"}
              {...register("constraints")} />
          </div>
        </Card>

        <Card className="space-y-4 p-5">
          <div>
            <h2 className="text-lg font-bold" id="visitor-label">Who's travelling?</h2>
            <p className="mt-1 text-sm text-ink-soft">
              Used only for this search, to apply entry rules and ticket prices that come with a source.
            </p>
          </div>
          <div role="group" aria-labelledby="visitor-label" className="grid gap-2 sm:grid-cols-2">
            {VISITOR_OPTIONS.map((o) => (
              <label key={o.key}
                className="flex cursor-pointer items-start gap-3 rounded-[var(--radius-control)] bg-white p-3 ring-1 ring-line has-[:checked]:bg-[#e6efe9] has-[:checked]:ring-sal">
                <input type="checkbox" className="mt-1 size-4 accent-[var(--color-sal)]" {...register(o.key)} />
                <span>
                  <span className="block text-sm font-semibold">{o.label}</span>
                  <span className="block text-xs text-ink-soft">{o.hint}</span>
                </span>
              </label>
            ))}
          </div>
          <div className="max-w-xs space-y-2">
            <Label htmlFor="maxfee">Highest entry fee per person (₹)</Label>
            <Input id="maxfee" type="number" inputMode="numeric" min={0} placeholder="No limit"
              aria-invalid={Boolean(errors.maxEntryFeeInr) || undefined}
              {...register("maxEntryFeeInr", { setValueAs: (v) => (v === "" || v === null ? undefined : Number(v)) })} />
            <FieldError message={errors.maxEntryFeeInr?.message} />
          </div>
        </Card>

        <Button type="submit" size="lg" disabled={isSubmitting}>Find places for me</Button>
      </form>
    </div>
  );
}
