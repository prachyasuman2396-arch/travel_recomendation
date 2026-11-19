import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Link, Route, Routes } from "react-router";
import { EmptyState } from "@/components/States";
import { Button } from "@/components/ui/button";
import { RecommendationProvider } from "@/context/RecommendationContext";
import { AppLayout } from "@/layouts/AppLayout";
import { AdminPage } from "@/pages/AdminPage";
import { DestinationPage } from "@/pages/DestinationPage";
import { ExplorePage } from "@/pages/ExplorePage";
import { LandingPage } from "@/pages/LandingPage";
import { MapPage } from "@/pages/MapPage";
import { ResultsPage } from "@/pages/ResultsPage";

export const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 60_000 } },
});

function NotFound() {
  return (
    <div className="mx-auto max-w-3xl px-5 py-12">
      <EmptyState title="This page doesn't exist" action={<Button asChild><Link to="/">Go home</Link></Button>} />
    </div>
  );
}

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<LandingPage />} />
        <Route path="explore" element={<ExplorePage />} />
        <Route path="results" element={<ResultsPage />} />
        <Route path="map" element={<MapPage />} />
        <Route path="destinations/:poiId" element={<DestinationPage />} />
        <Route path="admin" element={<AdminPage />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RecommendationProvider>
        <AppRoutes />
      </RecommendationProvider>
    </QueryClientProvider>
  );
}
