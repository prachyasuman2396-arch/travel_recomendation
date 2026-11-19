import type {
  AdminDashboard,
  AgentName,
  AgentReport,
  FestivalEvent,
  IngestionRun,
  IngestionSource,
  MatchCandidate,
  Page,
  Photo,
  PoiDetail,
  PoiFact,
  PoiSummary,
  PreferenceProfile,
  ProvidersResponse,
  RecommendationResult,
  RefineResponse,
  Review,
} from "@/types/api";

export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code: string,
    public details: Record<string, unknown> = {},
    public requestId: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }

  get retryable(): boolean {
    return this.status === 0 || this.status >= 500;
  }
}

interface ErrorEnvelope {
  error?: { code?: string; message?: string; details?: Record<string, unknown>; request_id?: string };
}

async function request<T>(path: string, init: RequestInit = {}, token?: string): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  } catch {
    throw new ApiError("Can't reach the server. Check your connection and that the API is running.", 0,
      "network_error");
  }
  if (!response.ok) {
    let body: ErrorEnvelope = {};
    try {
      body = (await response.json()) as ErrorEnvelope;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(
      body.error?.message ?? `Request failed (HTTP ${response.status})`,
      response.status,
      body.error?.code ?? "http_error",
      body.error?.details ?? {},
      body.error?.request_id ?? response.headers.get("X-Request-ID"),
    );
  }
  return (await response.json()) as T;
}

const qs = (params: Record<string, string | number | undefined | null>) => {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") search.set(k, String(v));
  });
  const s = search.toString();
  return s ? `?${s}` : "";
};

export const api = {
  recommend: (profile: PreferenceProfile, signal?: AbortSignal) =>
    request<RecommendationResult>("/api/v1/recommendations", {
      method: "POST", body: JSON.stringify(profile), signal,
    }),
  refine: (profile: PreferenceProfile, message: string) =>
    request<RefineResponse>("/api/v1/recommendations/refine", {
      method: "POST", body: JSON.stringify({ profile, message }),
    }),
  events: (params: { start?: string; days?: number } = {}) =>
    request<FestivalEvent[]>(`/api/v1/events${qs(params)}`),
  providers: () => request<ProvidersResponse>("/api/v1/recommendations/providers"),
  pois: (params: { limit?: number; offset?: number; category?: string; q?: string } = {}) =>
    request<Page<PoiSummary>>(`/api/v1/pois${qs(params)}`),
  poi: (id: string) => request<PoiDetail>(`/api/v1/pois/${encodeURIComponent(id)}`),
  poiReviews: (id: string) => request<Page<Review>>(`/api/v1/pois/${encodeURIComponent(id)}/reviews?limit=20`),
  /** Server redirects to a short-lived Google photo URL; the API key never reaches the browser. */
  googlePhotoUrl: (id: string, index = 0, maxWidth = 1200) =>
    `${API_BASE_URL}/api/v1/pois/${encodeURIComponent(id)}/photo${qs({ index, max_width: maxWidth })}`,
  poiPhotoAttributions: (id: string) =>
    request<{ index: number; attributions: { displayName?: string; uri?: string }[] }[]>(
      `/api/v1/pois/${encodeURIComponent(id)}/photo-attributions`),
  poiPhotos: (id: string) => request<Page<Photo>>(`/api/v1/pois/${encodeURIComponent(id)}/photos?limit=12`),

  adminDashboard: (token: string) => request<AdminDashboard>("/api/v1/admin/dashboard", {}, token),
  adminRuns: (token: string) => request<Page<IngestionRun>>("/api/v1/admin/ingestion?limit=20", {}, token),
  adminMatchCandidates: (token: string) =>
    request<MatchCandidate[]>("/api/v1/admin/match-candidates?limit=50", {}, token),
  runIngestion: (token: string, source: IngestionSource, body: Record<string, unknown>) =>
    request<IngestionRun>(`/api/v1/admin/ingestion/${source}`, { method: "POST", body: JSON.stringify(body) },
      token),
  loadCurated: (token: string) => request<IngestionRun>("/api/v1/admin/curated/load", { method: "POST" }, token),
  runAgent: (token: string, agent: AgentName) =>
    request<AgentReport>(`/api/v1/admin/agents/${agent}`, { method: "POST" }, token),
  adminFacts: (token: string, status: "unverified" | "verified" | "rejected" = "unverified") =>
    request<Page<PoiFact>>(`/api/v1/admin/facts${qs({ status, limit: 50 })}`, {}, token),
  decideFact: (token: string, factId: number, status: "verified" | "rejected") =>
    request<PoiFact>(`/api/v1/admin/facts/${factId}`, { method: "POST", body: JSON.stringify({ status }) }, token),
};
