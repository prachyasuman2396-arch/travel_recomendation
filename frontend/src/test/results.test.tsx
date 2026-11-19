import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DestinationCard } from "@/components/DestinationCard";
import { ResultsPage } from "@/pages/ResultsPage";
import { ApiError, api } from "@/services/api";
import { item, result } from "./fixtures";
import { renderWithProviders } from "./render";

const search = { profile: result().request, locationLabel: "Bhubaneswar" };

afterEach(() => vi.restoreAllMocks());

describe("ResultsPage", () => {
  it("asks the user to start when there is no search", () => {
    renderWithProviders(<ResultsPage />);
    expect(screen.getByText("Start by telling us where you are")).toBeInTheDocument();
  });

  it("shows a loading state, then ranked results with sample-data notice", async () => {
    let resolve!: (v: ReturnType<typeof result>) => void;
    vi.spyOn(api, "recommend").mockReturnValue(new Promise((r) => (resolve = r)));
    renderWithProviders(<ResultsPage />, { search });
    expect(screen.getByRole("status")).toHaveTextContent(/finding places/i);
    resolve(result());
    expect(await screen.findByText("Ekamra Kanan Botanical Gardens")).toBeInTheDocument();
    expect(screen.getByText(/offline demo ranking/i)).toBeInTheDocument();
    expect(screen.getByText(/viewing sample data/i)).toBeInTheDocument();
    expect(screen.getByText(/1 skipped because they're closed today/)).toBeInTheDocument();
    const items = screen.getByRole("list", { name: "Ranked recommendations" }).querySelectorAll(":scope > li");
    expect(items).toHaveLength(2);
  });

  it("shows a retryable error when every model fails, and retries on click", async () => {
    const spy = vi.spyOn(api, "recommend")
      .mockRejectedValueOnce(new ApiError("Recommendations are temporarily unavailable.", 503,
        "recommendation_unavailable", {}, "abc123"))
      .mockResolvedValueOnce(result());
    renderWithProviders(<ResultsPage />, { search });
    expect(await screen.findByText("We couldn't build your recommendations")).toBeInTheDocument();
    expect(screen.getByText("Reference: abc123")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(await screen.findByText("Deras Dam")).toBeInTheDocument();
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("explains an empty result", async () => {
    vi.spyOn(api, "recommend").mockResolvedValue(result({ recommendations: [], candidates_found: 0 }));
    renderWithProviders(<ResultsPage />, { search });
    expect(await screen.findByText("Nothing matched within your distance")).toBeInTheDocument();
  });
});

describe("DestinationCard", () => {
  it("renders facts, reasons and considerations", () => {
    renderWithProviders(<DestinationCard item={item()} />);
    expect(screen.getByLabelText("Rank 1")).toBeInTheDocument();
    expect(screen.getByText("1.5 km away")).toBeInTheDocument();
    expect(screen.getByText("Good weather for it")).toBeInTheDocument();
    expect(screen.getByText("Reviewers raised: food")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View details" })).toHaveAttribute("href", "/destinations/POI000001");
    expect(screen.getByRole("img", { name: /no photo available/i })).toBeInTheDocument();
  });

  it("is honest about missing ratings and tight time", () => {
    renderWithProviders(<DestinationCard item={item({ facts: { ...item().facts, rating: null, time_fit: "tight" },
      weather_suitability: "unknown" })} />);
    expect(screen.getByText("No ratings yet")).toBeInTheDocument();
    expect(screen.getByText(/tight for your time/i)).toBeInTheDocument();
    expect(screen.getByText("Weather unknown")).toBeInTheDocument();
  });
});

describe("India context", () => {
  const india = result({
    understood: ["temple entry rules for non-Hindu visitors", "4 hours available"],
    season: "Post-monsoon",
    air_quality: { us_aqi: 112, pm2_5: 40, pm10: 80, category: "unhealthy_sensitive" },
    events: [{ name: "Kartika Purnima / Boita Bandana", start: "2026-11-24T00:00:00+05:30",
      end: "2026-11-24T23:59:00+05:30", location_name: "Bindu Sagar, Bhubaneswar", crowd_impact: "high",
      description: "Crowds gather before dawn.", date_note: null, source_url: "https://example.org/cal" }],
    itinerary: { start: "2026-11-23T09:00:00+05:30", end: "2026-11-23T12:30:00+05:30", total_travel_minutes: 35,
      travel_method: "estimate", skipped: ["Deras Dam"], notes: ["Travel times are estimates."],
      stops: [{ poi_id: "POI000001", name: "Ekamra Kanan Botanical Gardens", arrive: "2026-11-23T09:10:00+05:30",
        depart: "2026-11-23T10:10:00+05:30", travel_minutes: 10, wait_minutes: 0, visit_minutes: 60,
        hours_known: true }] },
    excluded: { beyond_max_distance: 0, closed_today: 0, entry_restricted: 1 },
    recommendations: [item({
      name: "Lingaraj Temple", local_names: ["ଲିଙ୍ଗରାଜ ମନ୍ଦିର"],
      notices: [
        { kind: "fee", text: "Free entry", severity: "info", source_url: null, verified: true },
        { kind: "entry", text: "Non-Hindus may not enter; a viewing platform is outside.", severity: "critical",
          source_url: "https://odishatourism.gov.in/", verified: true },
      ],
      cited_facts: [{ id: "F1", key: "non_hindu_entry", text: "Non-Hindus may not enter", verified: true,
        source_name: "Odisha Tourism", source_url: "https://odishatourism.gov.in/" }],
    })],
  });

  it("shows understood constraints, season, AQI, festivals, itinerary and rule skips", async () => {
    vi.spyOn(api, "recommend").mockResolvedValue(india);
    renderWithProviders(<ResultsPage />, { search });
    expect(await screen.findByText("temple entry rules for non-Hindu visitors")).toBeInTheDocument();
    expect(screen.getByText("Post-monsoon")).toBeInTheDocument();
    expect(screen.getByText("AQI 112")).toBeInTheDocument();
    expect(screen.getByText("Kartika Purnima / Boita Bandana")).toBeInTheDocument();
    expect(screen.getByText("A plan that fits your time")).toBeInTheDocument();
    expect(screen.getByText(/Didn't fit: Deras Dam/)).toBeInTheDocument();
    expect(screen.getByText(/1 skipped because entry is restricted/)).toBeInTheDocument();
  });

  it("puts critical entry notices first, with their source, and lists cited facts", () => {
    renderWithProviders(<DestinationCard item={india.recommendations[0]} />);
    const notes = screen.getByLabelText("Before you go");
    expect(notes.firstElementChild).toHaveTextContent(/Non-Hindus may not enter/);
    expect(screen.getByText("ଲିଙ୍ଗରାଜ ମନ୍ଦିର")).toBeInTheDocument();
    expect(screen.getByText(/Based on 1 sourced fact/)).toBeInTheDocument();
  });

  it("refines results from a chat message without a second recommend call", async () => {
    const recommend = vi.spyOn(api, "recommend").mockResolvedValue(result());
    const refined = { ...result().request, available_hours: 2 };
    const refine = vi.spyOn(api, "refine").mockResolvedValue({
      profile: refined, changes: ["2 hours available"],
      result: result({ recommendations: [item({ name: "Bindu Sagar Lake" })] }),
    });
    renderWithProviders(<ResultsPage />, { search });
    await screen.findByText("Deras Dam");
    await userEvent.type(screen.getByLabelText("Change these results"), "sirf do ghante");
    await userEvent.click(screen.getByRole("button", { name: /update/i }));
    expect(await screen.findByText("Bindu Sagar Lake")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("2 hours available");
    expect(refine).toHaveBeenCalledWith(result().request, "sirf do ghante");
    expect(recommend).toHaveBeenCalledTimes(1);
  });
});
