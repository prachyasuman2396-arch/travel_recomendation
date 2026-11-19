import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { RecommendationProvider } from "@/context/RecommendationContext";
import type { PreferenceProfile } from "@/types/api";

export function renderWithProviders(ui: ReactNode, { route = "/", search = null as null | {
  profile: PreferenceProfile; locationLabel: string;
} } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RecommendationProvider initial={search}>
        <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
      </RecommendationProvider>
    </QueryClientProvider>,
  );
}
