import { useQuery } from "@tanstack/react-query";
import { api } from "@/services/api";
import type { PreferenceProfile } from "@/types/api";

/**
 * Results and Map pages share this query (same key -> one LLM call, cached).
 * No automatic retries: a failure is shown with an explicit "Try again" button.
 */
export function useRecommendations(profile: PreferenceProfile | null | undefined) {
  return useQuery({
    queryKey: ["recommendations", profile],
    queryFn: ({ signal }) => api.recommend(profile as PreferenceProfile, signal),
    enabled: Boolean(profile),
    staleTime: Infinity,
    gcTime: 30 * 60 * 1000,
    retry: false,
    refetchOnWindowFocus: false,
  });
}
