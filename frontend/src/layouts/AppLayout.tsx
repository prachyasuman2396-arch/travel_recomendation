import { NavLink, Outlet } from "react-router";
import { cn } from "@/lib/utils";

const links = [
  { to: "/explore", label: "Plan a trip" },
  { to: "/results", label: "Results" },
  { to: "/map", label: "Map" },
];

export function Logo() {
  return (
    <span className="flex items-center gap-2 text-lg font-extrabold tracking-tight">
      <svg viewBox="0 0 32 32" className="size-7" aria-hidden>
        <circle cx="16" cy="16" r="14" fill="none" stroke="currentColor" strokeWidth="3" />
        <circle cx="16" cy="16" r="7" fill="none" stroke="currentColor" strokeWidth="2" opacity=".45" />
        <circle cx="16" cy="16" r="3.5" fill="#E0A32A" />
      </svg>
      Radius
    </span>
  );
}

export function AppLayout() {
  return (
    <div className="flex min-h-dvh flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2">
        Skip to content
      </a>
      <header className="sticky top-0 z-40 border-b border-line/70 bg-paper/90 backdrop-blur">
        <nav className="mx-auto flex h-16 max-w-6xl items-center justify-between px-5" aria-label="Main">
          <NavLink to="/" className="text-sal-deep" aria-label="Radius home">
            <Logo />
          </NavLink>
          <ul className="flex items-center gap-1 text-sm font-medium">
            {links.map((l) => (
              <li key={l.to}>
                <NavLink
                  to={l.to}
                  className={({ isActive }) =>
                    cn("rounded-full px-3 py-2 transition-colors hover:bg-mist", isActive && "bg-mist text-sal-deep")}
                >
                  {l.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </header>
      <main id="main" className="flex-1">
        <Outlet />
      </main>
      <footer className="mt-24 border-t border-line bg-white">
        <div className="mx-auto grid max-w-6xl gap-8 px-5 py-10 text-sm text-ink-soft md:grid-cols-[1fr_auto]">
          <div className="max-w-prose space-y-2">
            <Logo />
            <p>
              Suggestions are ranked from listed ratings, reviews, distance and current weather. Distances are
              straight-line; always check opening hours and local conditions before you travel.
            </p>
            <p>
              Map data © <a className="underline" href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>.
              Photos © their Flickr owners. Weather by <a className="underline" href="https://open-meteo.com/">Open-Meteo</a>.
            </p>
          </div>
          <NavLink to="/admin" className="self-end text-ink-soft underline-offset-4 hover:underline">
            Data management
          </NavLink>
        </div>
      </footer>
    </div>
  );
}
