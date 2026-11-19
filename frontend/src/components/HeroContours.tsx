/**
 * The hero's signature visual: a topographic contour field with your pin and a
 * radius ring that draws once on load (static under prefers-reduced-motion).
 * Pure SVG — no external imagery.
 */
export function HeroContours() {
  const contours = Array.from({ length: 11 }, (_, i) => i);
  return (
    <svg viewBox="0 0 600 480" className="h-full w-full" role="img" aria-label="Contour map with a search radius around your location">
      <defs>
        <radialGradient id="glow" cx="58%" cy="52%" r="45%">
          <stop offset="0" stopColor="#E0A32A" stopOpacity=".18" />
          <stop offset="1" stopColor="#E0A32A" stopOpacity="0" />
        </radialGradient>
      </defs>
      <g fill="none" stroke="#2E6A4E" strokeOpacity=".28" strokeWidth="1.1">
        {contours.map((i) => (
          <path
            key={i}
            d={`M${-40 + i * 6},${70 + i * 34} C ${120 + i * 8},${20 + i * 30} ${260 - i * 4},${150 + i * 26} ${380 + i * 3},${
              90 + i * 30
            } S ${560 - i * 6},${40 + i * 36} ${660},${110 + i * 30}`}
          />
        ))}
        {[0, 1, 2, 3].map((i) => (
          <ellipse key={`hill-${i}`} cx="150" cy="330" rx={30 + i * 22} ry={18 + i * 14} transform="rotate(-18 150 330)" />
        ))}
      </g>
      <circle cx="350" cy="250" r="190" fill="url(#glow)" />
      <circle className="ring-draw" cx="350" cy="250" r="170" fill="none" stroke="#2E6A4E" strokeWidth="2.2" strokeDasharray="1200" />
      <circle cx="350" cy="250" r="85" fill="none" stroke="#2E6A4E" strokeOpacity=".35" strokeWidth="1.2" strokeDasharray="4 6" />
      {[
        [300, 170, 1], [452, 300, 2], [262, 318, 3],
      ].map(([x, y, n]) => (
        <g key={n}>
          <circle cx={x} cy={y} r="15" fill="#E0A32A" stroke="white" strokeWidth="2.5" />
          <text x={x} y={y + 5} textAnchor="middle" fontSize="14" fontWeight="800" fill="#18262E">{n}</text>
        </g>
      ))}
      <circle cx="350" cy="250" r="9" fill="#2B5C88" stroke="white" strokeWidth="3" />
      <text x="372" y="92" fontSize="13" fontWeight="600" fill="#2E6A4E">20 km</text>
    </svg>
  );
}
