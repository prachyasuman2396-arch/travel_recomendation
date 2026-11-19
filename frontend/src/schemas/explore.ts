import { z } from "zod";
import type { PreferenceProfile } from "@/types/api";

export const interestEnum = z.enum([
  "nature", "adventure", "historical", "religious", "cultural", "food", "entertainment", "shopping",
  "wildlife", "photography", "beaches", "waterfalls", "parks_gardens",
]);

/** Mirrors backend PreferenceProfile validation so most errors are caught before the request. */
export const exploreSchema = z.object({
  locationLabel: z.string().min(1, "Choose a starting location"),
  latitude: z.number({ invalid_type_error: "Choose a starting location" }).min(-90).max(90),
  longitude: z.number({ invalid_type_error: "Choose a starting location" }).min(-180).max(180),
  interests: z.array(interestEnum).max(13),
  naturalLanguage: z.string().max(1000, "Keep this under 1000 characters").optional(),
  maxDistanceKm: z.number().min(1, "At least 1 km").max(100, "At most 100 km"),
  availableHours: z.number().min(0.5, "At least 30 minutes").max(24, "At most 24 hours"),
  budget: z.enum(["low", "medium", "high"]).optional(),
  travelType: z.enum(["solo", "couple", "family", "friends", "group"]).optional(),
  constraints: z.string().max(1000).optional(),
  // Who is travelling (India): temple entry rules, ticket prices, accessibility, crowds
  nonHinduVisitor: z.boolean(),
  foreignNational: z.boolean(),
  wheelchairUser: z.boolean(),
  withChildren: z.boolean(),
  withElderly: z.boolean(),
  avoidCrowds: z.boolean(),
  maxEntryFeeInr: z.number().int().min(0, "Can't be negative").max(100000).optional(),
}).refine((v) => v.interests.length > 0 || (v.naturalLanguage ?? "").trim().length > 0, {
  message: "Pick at least one interest or describe what you want",
  path: ["interests"],
});

export type ExploreValues = z.infer<typeof exploreSchema>;

export const exploreDefaults: ExploreValues = {
  locationLabel: "",
  latitude: Number.NaN,
  longitude: Number.NaN,
  interests: [],
  naturalLanguage: "",
  maxDistanceKm: 20,
  availableHours: 5,
  budget: undefined,
  travelType: undefined,
  constraints: "",
  nonHinduVisitor: false,
  foreignNational: false,
  wheelchairUser: false,
  withChildren: false,
  withElderly: false,
  avoidCrowds: false,
  maxEntryFeeInr: undefined,
};

export function toPreferenceProfile(v: ExploreValues, now: Date = new Date()): PreferenceProfile {
  const constraints = (v.constraints ?? "")
    .split(/\n|;/)
    .map((c) => c.trim())
    .filter(Boolean)
    .slice(0, 10);
  // Local time WITH offset, so "closed today" uses the traveller's day, not the server's.
  const offsetMin = -now.getTimezoneOffset();
  const sign = offsetMin >= 0 ? "+" : "-";
  const pad = (n: number) => String(Math.floor(Math.abs(n))).padStart(2, "0");
  const local = new Date(now.getTime() + offsetMin * 60000).toISOString().slice(0, 19);
  return {
    latitude: v.latitude,
    longitude: v.longitude,
    interests: v.interests,
    natural_language: v.naturalLanguage?.trim() || null,
    max_distance_km: v.maxDistanceKm,
    available_hours: v.availableHours,
    budget: v.budget ?? null,
    travel_type: v.travelType ?? null,
    constraints,
    client_datetime: `${local}${sign}${pad(offsetMin / 60)}:${pad(offsetMin % 60)}`,
    visitor: {
      non_hindu_visitor: v.nonHinduVisitor,
      foreign_national: v.foreignNational,
      wheelchair_user: v.wheelchairUser,
      with_children: v.withChildren,
      with_elderly: v.withElderly,
      avoid_crowds: v.avoidCrowds,
      max_entry_fee_inr: v.maxEntryFeeInr ?? null,
      avoid_categories: [],
    },
  };
}
