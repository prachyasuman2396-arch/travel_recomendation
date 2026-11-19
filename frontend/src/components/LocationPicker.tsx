import { LocateFixed, MapPin, Search } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/primitives";
import { useGeolocation } from "@/hooks/useGeolocation";
import { parseCoordinates, searchPlaces, type Place } from "@/services/geocode";

/**
 * Location input: browser geolocation (only on click) or manual search.
 * Rendered as a <div>, not a <form>, so it can live inside the Explore form.
 */
export function LocationPicker({ value, onChange, invalid }: {
  value: Place | null;
  onChange: (p: Place) => void;
  invalid?: boolean;
}) {
  const { state, locate } = useGeolocation();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Place[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  useEffect(() => {
    if (state.status === "ok") {
      onChange({ label: "Your current location", latitude: state.latitude, longitude: state.longitude });
    }
    // onChange identity may change every render; only react to new coordinates
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state]);

  async function runSearch(e?: FormEvent) {
    e?.preventDefault();
    setSearchError(null);
    const coords = parseCoordinates(query);
    if (coords) {
      onChange(coords);
      setResults([]);
      return;
    }
    setSearching(true);
    try {
      const found = await searchPlaces(query);
      setResults(found);
      if (found.length === 0) setSearchError("No places found. Try a nearby town or enter coordinates.");
    } catch (err) {
      setSearchError(err instanceof Error ? err.message : "Search failed.");
    } finally {
      setSearching(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-2 sm:flex-row">
        <Button type="button" variant="secondary" onClick={locate} disabled={state.status === "locating"}>
          <LocateFixed /> {state.status === "locating" ? "Finding you…" : "Use my location"}
        </Button>
        <div className="flex flex-1 gap-2">
          <Input
            aria-label="Search for a town, landmark or coordinates"
            aria-invalid={invalid || undefined}
            placeholder="Search a place, or type 20.2961, 85.8245"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void runSearch(e);
            }}
          />
          <Button type="button" variant="secondary" onClick={() => void runSearch()} disabled={searching || query.trim().length < 2}
            aria-label="Search location">
            <Search />
          </Button>
        </div>
      </div>

      {state.status === "error" && <p className="text-sm text-danger">{state.message}</p>}
      {searchError && <p className="text-sm text-danger">{searchError}</p>}

      {results.length > 0 && (
        <ul className="divide-y divide-line overflow-hidden rounded-[var(--radius-control)] bg-white ring-1 ring-line" aria-label="Search results">
          {results.map((r) => (
            <li key={`${r.latitude},${r.longitude}`}>
              <button
                type="button"
                className="w-full px-3.5 py-2.5 text-left text-sm hover:bg-mist"
                onClick={() => {
                  onChange(r);
                  setResults([]);
                }}
              >
                {r.label}
              </button>
            </li>
          ))}
          <li className="px-3.5 py-1.5 text-xs text-ink-soft">Search by OpenStreetMap Nominatim</li>
        </ul>
      )}

      {value && (
        <p className="flex items-center gap-2 text-sm">
          <MapPin className="size-4 text-sal" aria-hidden />
          <span className="font-semibold">{value.label}</span>
          <span className="num text-ink-soft">
            ({value.latitude.toFixed(4)}, {value.longitude.toFixed(4)})
          </span>
        </p>
      )}
    </div>
  );
}
