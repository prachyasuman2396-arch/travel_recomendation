import { useCallback, useState } from "react";

export type GeoState =
  | { status: "idle" }
  | { status: "locating" }
  | { status: "ok"; latitude: number; longitude: number; accuracy: number }
  | { status: "error"; message: string };

const MESSAGES: Record<number, string> = {
  1: "Location permission was denied. Search for a place instead.",
  2: "Your location couldn't be determined. Search for a place instead.",
  3: "Finding your location took too long. Try again or search for a place.",
};

/** Asks for the browser's location only when the user clicks — never on page load. */
export function useGeolocation() {
  const [state, setState] = useState<GeoState>({ status: "idle" });
  const locate = useCallback(() => {
    if (!("geolocation" in navigator)) {
      setState({ status: "error", message: "This browser can't share its location. Search for a place instead." });
      return;
    }
    setState({ status: "locating" });
    navigator.geolocation.getCurrentPosition(
      (pos) => setState({ status: "ok", latitude: pos.coords.latitude, longitude: pos.coords.longitude,
        accuracy: pos.coords.accuracy }),
      (err) => setState({ status: "error", message: MESSAGES[err.code] ?? "Location is unavailable." }),
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 },
    );
  }, []);
  return { state, locate };
}
