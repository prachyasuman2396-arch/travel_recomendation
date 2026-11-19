import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MapPage } from "@/pages/MapPage";
import { ApiError, api } from "@/services/api";
import { parseCoordinates } from "@/services/geocode";
import { result } from "./fixtures";
import { renderWithProviders } from "./render";

// Leaflet needs a real layout engine; render markers as plain elements so we can assert on them.
vi.mock("react-leaflet", () => ({
  MapContainer: ({ children }: { children: ReactNode }) => <div data-testid="map">{children}</div>,
  TileLayer: () => null,
  Circle: () => <div data-testid="radius" />,
  Popup: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Marker: ({ title, children }: { title: string; children: ReactNode }) => (
    <div data-testid="marker" title={title}>{children}</div>
  ),
  useMap: () => ({ fitBounds: vi.fn() }),
}));

afterEach(() => vi.restoreAllMocks());

describe("MapPage", () => {
  it("renders the user, recommended and other candidate markers", async () => {
    vi.spyOn(api, "recommend").mockResolvedValue(result());
    renderWithProviders(<MapPage />, { search: { profile: result().request, locationLabel: "Bhubaneswar" } });
    expect(await screen.findByTestId("map")).toBeInTheDocument();
    const titles = screen.getAllByTestId("marker").map((m) => m.getAttribute("title"));
    expect(titles).toEqual(expect.arrayContaining(["Your starting point", "Ekamra Kanan Botanical Gardens",
      "Deras Dam", "Esplanade One Mall"]));
    expect(screen.getByTestId("radius")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Deras Dam/ }));
    expect(screen.getAllByText(/1.5 km away/).length).toBeGreaterThan(0);
  });
});

describe("api client", () => {
  it("maps the backend error envelope to ApiError", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      error: { code: "validation_error", message: "Request validation failed", details: {}, request_id: "r1" },
    }), { status: 422 }));
    const err = await api.providers().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 422, code: "validation_error", requestId: "r1", retryable: false });
  });

  it("reports network failures as retryable", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    const err = await api.providers().catch((e) => e);
    expect(err).toMatchObject({ status: 0, code: "network_error", retryable: true });
  });

  it("parses typed coordinates", () => {
    expect(parseCoordinates("20.2961, 85.8245")).toMatchObject({ latitude: 20.2961, longitude: 85.8245 });
    expect(parseCoordinates("95, 85")).toBeNull();
    expect(parseCoordinates("Bhubaneswar")).toBeNull();
  });
});
