import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ExplorePage } from "@/pages/ExplorePage";
import { exploreDefaults, exploreSchema, toPreferenceProfile } from "@/schemas/explore";
import { renderWithProviders } from "./render";

const valid = { ...exploreDefaults, locationLabel: "Bhubaneswar", latitude: 20.2961, longitude: 85.8245,
  interests: ["nature" as const] };

describe("explore schema", () => {
  it("requires a location", () => {
    const r = exploreSchema.safeParse(exploreDefaults);
    expect(r.success).toBe(false);
  });

  it("requires an interest or a description", () => {
    const r = exploreSchema.safeParse({ ...valid, interests: [], naturalLanguage: "" });
    expect(r.success).toBe(false);
    expect(exploreSchema.safeParse({ ...valid, interests: [], naturalLanguage: "quiet lakes" }).success).toBe(true);
  });

  it("bounds distance and time like the backend", () => {
    expect(exploreSchema.safeParse({ ...valid, maxDistanceKm: 0 }).success).toBe(false);
    expect(exploreSchema.safeParse({ ...valid, availableHours: 30 }).success).toBe(false);
  });

  it("maps form values to the API profile with a local time offset", () => {
    const profile = toPreferenceProfile({ ...valid, constraints: "Wheelchair access\n\nNo climbs" },
      new Date("2026-11-16T04:00:00Z"));
    expect(profile.constraints).toEqual(["Wheelchair access", "No climbs"]);
    expect(profile.natural_language).toBeNull();
    expect(profile.client_datetime).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$/);
    expect(profile.visitor?.non_hindu_visitor).toBe(false);
    expect(profile.visitor?.max_entry_fee_inr).toBeNull();
  });

  it("maps who's travelling to the visitor profile", () => {
    const profile = toPreferenceProfile({ ...valid, nonHinduVisitor: true, foreignNational: true, maxEntryFeeInr: 200 });
    expect(profile.visitor).toMatchObject({ non_hindu_visitor: true, foreign_national: true, max_entry_fee_inr: 200 });
    expect(exploreSchema.safeParse({ ...valid, maxEntryFeeInr: -5 }).success).toBe(false);
  });
});

describe("ExplorePage", () => {
  it("shows validation errors and does not navigate when location is missing", async () => {
    renderWithProviders(<ExplorePage />, { route: "/explore" });
    await userEvent.click(screen.getByRole("button", { name: "Find places for me" }));
    expect(await screen.findByText("Choose a starting location")).toBeInTheDocument();
  });

  it("prefills interest from the URL and toggles interest buttons", async () => {
    renderWithProviders(<ExplorePage />, { route: "/explore?interest=wildlife" });
    const wildlife = screen.getByRole("button", { name: "Wildlife" });
    expect(wildlife).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(wildlife);
    expect(wildlife).toHaveAttribute("aria-pressed", "false");
  });

  it("accepts coordinates typed into the search box", async () => {
    renderWithProviders(<ExplorePage />, { route: "/explore" });
    await userEvent.type(screen.getByLabelText(/search for a town/i), "20.2961, 85.8245{enter}");
    expect(await screen.findByText("(20.2961, 85.8245)")).toBeInTheDocument();
  });
});
