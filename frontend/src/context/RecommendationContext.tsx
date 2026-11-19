import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { safeStorage } from "@/lib/utils";
import type { PreferenceProfile } from "@/types/api";

const KEY = "radius.lastRequest";

interface SearchState {
  profile: PreferenceProfile;
  locationLabel: string;
}

interface Ctx {
  search: SearchState | null;
  setSearch: (s: SearchState) => void;
  clear: () => void;
}

const RecommendationContext = createContext<Ctx | null>(null);

export function RecommendationProvider({ children, initial = null }: { children: ReactNode; initial?: SearchState | null }) {
  const [search, setState] = useState<SearchState | null>(() => initial ?? safeStorage.get<SearchState>(KEY));
  const setSearch = useCallback((s: SearchState) => {
    setState(s);
    safeStorage.set(KEY, s);
  }, []);
  const clear = useCallback(() => {
    setState(null);
    safeStorage.remove(KEY);
  }, []);
  const value = useMemo(() => ({ search, setSearch, clear }), [search, setSearch, clear]);
  return <RecommendationContext.Provider value={value}>{children}</RecommendationContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useSearch(): Ctx {
  const ctx = useContext(RecommendationContext);
  if (!ctx) throw new Error("useSearch must be used inside RecommendationProvider");
  return ctx;
}
