/** Mirrors backend Pydantic schemas (app/schemas/*). Keep in sync. */

export type Interest =
  | "nature" | "adventure" | "historical" | "religious" | "cultural" | "food" | "entertainment"
  | "shopping" | "wildlife" | "photography" | "beaches" | "waterfalls" | "parks_gardens";

export type TravelType = "solo" | "couple" | "family" | "friends" | "group";
export type Budget = "low" | "medium" | "high";
export type WeatherSuitability = "suitable" | "acceptable" | "unsuitable" | "unknown";

/** Who is travelling. Drives temple-entry rules, ticket prices, accessibility, crowds. */
export interface VisitorProfile {
  non_hindu_visitor: boolean;
  foreign_national: boolean;
  wheelchair_user: boolean;
  with_children: boolean;
  with_elderly: boolean;
  avoid_crowds: boolean;
  max_entry_fee_inr: number | null;
  avoid_categories: string[];
}

export interface PreferenceProfile {
  latitude: number;
  longitude: number;
  interests: Interest[];
  natural_language?: string | null;
  max_distance_km: number;
  available_hours: number;
  budget?: Budget | null;
  travel_type?: TravelType | null;
  constraints: string[];
  client_datetime?: string | null;
  visitor?: VisitorProfile;
}

export type Severity = "info" | "warning" | "critical";

/** Computed by code from sourced facts and the festival calendar — never written by the AI model. */
export interface Notice {
  kind: string; // entry | fee | accessibility | crowd | season | phones | dress
  text: string;
  severity: Severity;
  source_url: string | null;
  verified: boolean;
}

export interface Fact {
  id: string;
  key: string;
  text: string;
  verified: boolean;
  source_name: string | null;
  source_url: string;
}

export interface AirQuality {
  us_aqi: number | null;
  pm2_5: number | null;
  pm10: number | null;
  category: string | null;
}

export interface ItineraryStop {
  poi_id: string;
  name: string;
  arrive: string;
  depart: string;
  travel_minutes: number;
  wait_minutes: number;
  visit_minutes: number;
  hours_known: boolean;
}

export interface Itinerary {
  start: string;
  end: string;
  stops: ItineraryStop[];
  total_travel_minutes: number;
  travel_method: "osrm" | "estimate";
  skipped: string[];
  notes: string[];
}

export interface FestivalEvent {
  name: string;
  start: string;
  end: string;
  location_name: string;
  crowd_impact: "low" | "medium" | "high" | "extreme" | string;
  description: string;
  date_note: string | null;
  source_url: string;
}

export interface RecommendationFacts {
  distance_km: number;
  rating: number | null;
  rating_count: number;
  rating_sources: string[];
  review_count: number;
  photo_count: number;
  photo_activity: "none" | "low" | "medium" | "high";
  sentiment_status: string;
  sentiment_overall: string | null;
  positive_aspects: string[];
  negative_aspects: string[];
  hours_today: string | null;
  time_fit: "fits" | "tight" | "unlikely";
  data_origin: "mock" | "live";
}

export interface RecommendationItem {
  poi_id: string;
  rank: number;
  name: string;
  category: string;
  categories: string[];
  latitude: number;
  longitude: number;
  address: string | null;
  website: string | null;
  photo_url: string | null;
  facts: RecommendationFacts;
  reason: string;
  matched_preferences: string[];
  advantages: string[];
  considerations: string[];
  weather_suitability: WeatherSuitability;
  review_insights: string[];
  notices?: Notice[];
  cited_facts?: Fact[];
  place_facts?: Fact[];
  season_note?: string | null;
  local_names?: string[];
  image_url?: string | null;
  image_attribution?: string | null;
  wiki_url?: string | null;
}

export interface CandidateMarker {
  poi_id: string;
  name: string;
  category: string;
  latitude: number;
  longitude: number;
  distance_km: number;
  recommended: boolean;
}

export interface WeatherSummary {
  source: string;
  is_mock: boolean;
  retrieved_at: string;
  observed_at: string | null;
  condition: string | null;
  temperature_c: number | null;
  humidity: number | null;
  wind_speed_kmh: number | null;
  max_precipitation_probability: number | null;
}

export type GenerationMethod = "llm" | "mock_llm" | "deterministic_fallback" | "none";

export interface RecommendationResult {
  request: PreferenceProfile;
  recommendations: RecommendationItem[];
  candidates: CandidateMarker[];
  summary: string;
  generation_method: GenerationMethod;
  provider: string | null;
  model: string | null;
  fallback_used: boolean;
  weather_status: "available" | "unavailable" | "disabled";
  weather: WeatherSummary | null;
  candidates_found: number;
  candidates_sent: number;
  excluded: Record<string, number>;
  data_origin: "mock" | "live" | "mixed" | "none";
  warnings: string[];
  generated_at: string;
  disclaimer: string;
  understood?: string[];
  intent_method?: string;
  season?: string | null;
  air_quality?: AirQuality | null;
  events?: FestivalEvent[];
  itinerary?: Itinerary | null;
  semantic_method?: string;
  timings_ms?: Record<string, number>;
}

export interface RefineResponse {
  profile: PreferenceProfile;
  changes: string[];
  result: RecommendationResult;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface PoiSummary {
  poi_id: string;
  name: string;
  category: string;
  categories: string[];
  latitude: number;
  longitude: number;
  address: string | null;
  data_origin: "mock" | "live";
  distance_km: number | null;
}

export interface SentimentSummary {
  status: "available" | "insufficient_data";
  reviews_analyzed: number;
  overall: string | null;
  positive_share: number | null;
  neutral_share: number | null;
  negative_share: number | null;
  aspects: Record<string, { label: string; mentions: number }>;
  model_name: string;
}

export interface PoiDetail extends PoiSummary {
  description: string | null;
  website: string | null;
  opening_hours: { weekdayDescriptions?: string[] } | null;
  primary_source: string;
  rating: number | null;
  rating_count: number;
  ratings: { source: string; rating: number; rating_count: number; fetched_at: string }[];
  sources: { provider: string; external_id: string; matching_confidence: number; matching_method: string }[];
  sentiment: SentimentSummary | null;
  review_count: number;
  photo_count: number;
  updated_at: string;
  alt_names?: string[];
  facts?: PoiFact[];
  background?: PoiBackground | null;
  google_photo_count?: number;
}

export interface PoiFact {
  fact_id: number;
  poi_id: string;
  fact_key: string;
  value: Record<string, unknown>;
  display_text: string;
  source_type: string;
  source_name: string | null;
  source_url: string;
  evidence: string | null;
  status: "verified" | "unverified" | "rejected";
  extracted_by: string | null;
  checked_at: string | null;
}

export interface PoiBackground {
  source: string;
  url: string;
  title: string | null;
  summary: string | null;
  license: string | null;
  image_url: string | null;
  image_attribution: string | null;
  data_origin: "mock" | "live";
}

export interface Review {
  review_id: number;
  source: string;
  rating: number | null;
  review_text: string | null;
  review_date: string | null;
  author_name?: string | null;
  author_url?: string | null;
  language?: string | null;
  data_origin: "mock" | "live";
}

export interface Photo {
  external_photo_id: string;
  title: string | null;
  tags: string[];
  owner_name: string | null;
  license_id: string | null;
  permitted_photo_url: string | null;
  page_url: string | null;
  match_confidence: number;
  match_method: string;
  data_origin: "mock" | "live";
}

export interface ProvidersResponse {
  providers: { role: string; provider: string; model: string; configured: boolean }[];
  fallback_enabled: boolean;
  deterministic_fallback_enabled: boolean;
}

export interface IngestionRun {
  run_id: string;
  source: string;
  mode: "mock" | "live";
  status: "running" | "succeeded" | "partial" | "failed";
  params: Record<string, unknown>;
  started_at: string | null;
  completed_at: string | null;
  records_fetched: number;
  records_inserted: number;
  records_updated: number;
  records_rejected: number;
  records_flagged: number;
  error_summary: string | null;
  error_details: { rejection_reasons?: Record<string, number>; notes?: string[] } | null;
}

export interface MatchCandidate {
  id: number;
  provider: string;
  external_id: string;
  candidate_poi_id: string;
  score: number;
  score_breakdown: Record<string, unknown>;
  source_payload: Record<string, unknown>;
  status: string;
}

export interface AdminDashboard {
  totals: Record<string, number>;
  pois_by_category: Record<string, number>;
  pois_by_origin: Record<string, number>;
  source_coverage: Record<string, number>;
  matching: { by_method: Record<string, number>; auto_confidence_buckets: Record<string, number> };
  data_quality: Record<string, number>;
  sentiment_status: Record<string, number>;
  ingestion_by_source: { source: string; runs: number; failed: number; last_started_at: string | null }[];
  integrations: Record<string, boolean>;
  llm: { primary: string; fallback: string | null; configured_chain: string[] };
}

export interface AgentReport {
  agent: string;
  processed: number;
  accepted: number;
  rejected: number;
  errors: number;
  notes: string[];
}

export type IngestionSource = "places" | "osm" | "wikipedia" | "yelp" | "flickr";
export type AgentName = "fact_extraction" | "match_judge" | "embeddings" | "freshness";
