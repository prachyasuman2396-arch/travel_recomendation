import L from "leaflet";
import { useEffect, useMemo } from "react";
import { Circle, MapContainer, Marker, Popup, TileLayer, useMap } from "react-leaflet";
import { Link } from "react-router";
import { formatKm } from "@/lib/utils";

export interface MapPoint {
  id: string;
  name: string;
  latitude: number;
  longitude: number;
  distanceKm?: number;
  rank?: number;
  subtitle?: string;
}

const userIcon = L.divIcon({ className: "", html: '<div class="pin pin-user"></div>', iconSize: [18, 18] });
const candidateIcon = L.divIcon({ className: "", html: '<div class="pin pin-candidate"></div>', iconSize: [12, 12] });
const rankIcon = (rank: number) =>
  L.divIcon({ className: "", html: `<div class="pin pin-rank">${rank}</div>`, iconSize: [28, 28] });

function FitBounds({ points }: { points: [number, number][] }) {
  const map = useMap();
  useEffect(() => {
    if (points.length > 1) map.fitBounds(L.latLngBounds(points), { padding: [40, 40], maxZoom: 14 });
  }, [map, points]);
  return null;
}

export function RadiusMap({ center, radiusKm, recommended, others = [], selectedId, onSelect, className }: {
  center: [number, number];
  radiusKm?: number;
  recommended: MapPoint[];
  others?: MapPoint[];
  selectedId?: string | null;
  onSelect?: (id: string) => void;
  className?: string;
}) {
  const bounds = useMemo<[number, number][]>(
    () => [center, ...recommended.map((p) => [p.latitude, p.longitude] as [number, number])],
    [center, recommended],
  );
  return (
    <MapContainer center={center} zoom={12} scrollWheelZoom className={className ?? "h-[480px] w-full rounded-[var(--radius-card)]"}>
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      <FitBounds points={bounds} />
      {radiusKm && (
        <Circle center={center} radius={radiusKm * 1000}
          pathOptions={{ color: "#2E6A4E", weight: 1.5, fillOpacity: 0.04, dashArray: "6 6" }} />
      )}
      <Marker position={center} icon={userIcon} title="Your starting point">
        <Popup>Your starting point</Popup>
      </Marker>
      {others.map((p) => (
        <Marker key={p.id} position={[p.latitude, p.longitude]} icon={candidateIcon} title={p.name}>
          <Popup>
            <strong>{p.name}</strong>
            {p.distanceKm !== undefined && <div>{formatKm(p.distanceKm)} away (straight line)</div>}
            <div>Considered, not in your top picks</div>
          </Popup>
        </Marker>
      ))}
      {recommended.map((p) => (
        <Marker
          key={p.id}
          position={[p.latitude, p.longitude]}
          icon={rankIcon(p.rank ?? 0)}
          title={p.name}
          zIndexOffset={selectedId === p.id ? 1000 : 500}
          eventHandlers={{ click: () => onSelect?.(p.id) }}
        >
          <Popup>
            <strong>
              {p.rank}. {p.name}
            </strong>
            {p.subtitle && <div>{p.subtitle}</div>}
            {p.distanceKm !== undefined && <div>{formatKm(p.distanceKm)} away (straight line)</div>}
            <Link to={`/destinations/${p.id}`}>View details</Link>
          </Popup>
        </Marker>
      ))}
    </MapContainer>
  );
}
