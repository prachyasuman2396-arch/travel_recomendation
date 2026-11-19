/**
 * Manual location search via OpenStreetMap Nominatim.
 * Usage policy: at most 1 request/second, no autocomplete-per-keystroke, attribution required.
 * We only search on explicit submit. https://operations.osmfoundation.org/policies/nominatim/
 */
export interface Place {
  label: string;
  latitude: number;
  longitude: number;
}

let lastCall = 0;

export async function searchPlaces(query: string, signal?: AbortSignal): Promise<Place[]> {
  const q = query.trim();
  if (q.length < 2) return [];
  const wait = 1000 - (Date.now() - lastCall);
  if (wait > 0) await new Promise((r) => setTimeout(r, wait));
  lastCall = Date.now();
  const url = `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=5&q=${encodeURIComponent(q)}`;
  const res = await fetch(url, { signal, headers: { "Accept-Language": "en" } });
  if (!res.ok) throw new Error("Location search is unavailable right now. Enter coordinates instead.");
  const data = (await res.json()) as { display_name: string; lat: string; lon: string }[];
  return data.map((d) => ({ label: d.display_name, latitude: Number(d.lat), longitude: Number(d.lon) }));
}

/** Parse "20.2961, 85.8245" typed directly into the search box. */
export function parseCoordinates(text: string): Place | null {
  const m = text.trim().match(/^(-?\d{1,2}(?:\.\d+)?)\s*[, ]\s*(-?\d{1,3}(?:\.\d+)?)$/);
  if (!m) return null;
  const latitude = Number(m[1]);
  const longitude = Number(m[2]);
  if (Math.abs(latitude) > 90 || Math.abs(longitude) > 180) return null;
  return { label: `${latitude.toFixed(4)}, ${longitude.toFixed(4)}`, latitude, longitude };
}
