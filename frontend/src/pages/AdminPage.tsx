import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { ErrorState } from "@/components/States";
import { Button } from "@/components/ui/button";
import { Badge, Card, Input, Label, Select, Skeleton } from "@/components/ui/primitives";
import { safeStorage } from "@/lib/utils";
import { ApiError, api } from "@/services/api";
import type { AgentName, AgentReport, IngestionRun, IngestionSource } from "@/types/api";

const TOKEN_KEY = "radius.adminToken";
const SOURCES: { key: IngestionSource; label: string }[] = [
  { key: "places", label: "Google Places" }, { key: "osm", label: "OpenStreetMap" },
  { key: "wikipedia", label: "Wikipedia" }, { key: "yelp", label: "Yelp" }, { key: "flickr", label: "Flickr" },
];
const AGENTS: { key: AgentName; label: string; hint: string }[] = [
  { key: "fact_extraction", label: "Extract facts", hint: "Reads Wikipedia/official text; proposes facts with a verbatim quote" },
  { key: "match_judge", label: "Judge matches", hint: "Decides uncertain duplicates; applies only confident verdicts" },
  { key: "embeddings", label: "Index for search", hint: "Gemini embeddings for 'peaceful lake'-style queries" },
  { key: "freshness", label: "Check freshness", hint: "Re-checks Google status, purges expired content" },
];
const STATUS_TONE = { succeeded: "good", partial: "warn", failed: "bad", running: "info" } as const;
const LABELS: Record<string, string> = {
  pois: "Destinations", source_records: "Source records", reviews: "Reviews", flickr_photos: "Flickr photos",
  yelp_businesses: "Yelp businesses", users: "Users", pending_match_reviews: "Matches to review",
};

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <Card className="p-4">
      <p className="text-sm text-ink-soft">{label}</p>
      <p className="num mt-1 text-2xl font-bold">{value}</p>
    </Card>
  );
}

function KeyValues({ title, data }: { title: string; data: Record<string, number | boolean> }) {
  const entries = Object.entries(data);
  return (
    <Card className="p-4">
      <h3 className="font-bold">{title}</h3>
      {entries.length === 0 ? <p className="mt-2 text-sm text-ink-soft">No data yet</p> : (
        <dl className="num mt-2 grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-sm">
          {entries.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-ink-soft">{k.replaceAll("_", " ")}</dt>
              <dd className="text-right font-semibold">{typeof v === "boolean" ? (v ? "configured" : "not set") : v}</dd>
            </div>
          ))}
        </dl>
      )}
    </Card>
  );
}

export function AdminPage() {
  const [token, setToken] = useState<string>(() => safeStorage.get<string>(TOKEN_KEY) ?? "");
  const [draft, setDraft] = useState("");
  const qc = useQueryClient();

  const dashboard = useQuery({ queryKey: ["admin", "dashboard", token], queryFn: () => api.adminDashboard(token),
    enabled: Boolean(token), retry: false });
  const runs = useQuery({ queryKey: ["admin", "runs", token], queryFn: () => api.adminRuns(token),
    enabled: dashboard.isSuccess });
  const matches = useQuery({ queryKey: ["admin", "matches", token], queryFn: () => api.adminMatchCandidates(token),
    enabled: dashboard.isSuccess });

  const [mode, setMode] = useState<"auto" | "mock" | "live">("auto");
  const ingest = useMutation({
    mutationFn: (source: IngestionSource) =>
      api.runIngestion(token, source, { latitude: 20.2961, longitude: 85.8245, radius_km: 30, mode }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["admin"] }),
  });
  const curated = useMutation({
    mutationFn: () => api.loadCurated(token),
    onSettled: () => qc.invalidateQueries({ queryKey: ["admin"] }),
  });
  const agent = useMutation<AgentReport, Error, AgentName>({
    mutationFn: (name) => api.runAgent(token, name),
    onSettled: () => qc.invalidateQueries({ queryKey: ["admin"] }),
  });
  const facts = useQuery({ queryKey: ["admin", "facts", token], queryFn: () => api.adminFacts(token),
    enabled: dashboard.isSuccess });
  const decide = useMutation({
    mutationFn: ({ id, status }: { id: number; status: "verified" | "rejected" }) => api.decideFact(token, id, status),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "facts"] }),
  });

  function signIn(e: FormEvent) {
    e.preventDefault();
    safeStorage.set(TOKEN_KEY, draft);
    setToken(draft);
  }

  const unauthorized = dashboard.error instanceof ApiError && [401, 503].includes(dashboard.error.status);

  if (!token || unauthorized) {
    return (
      <div className="mx-auto max-w-md px-5 py-16">
        <h1 className="text-3xl font-extrabold">Data management</h1>
        <p className="mt-2 text-ink-soft">Enter the admin token configured on the server (ADMIN_API_TOKEN).</p>
        <form onSubmit={signIn} className="mt-6 space-y-3">
          <Label htmlFor="token">Admin token</Label>
          <Input id="token" type="password" autoComplete="off" value={draft} onChange={(e) => setDraft(e.target.value)} />
          {unauthorized && <p role="alert" className="text-sm text-danger">{(dashboard.error as ApiError).message}</p>}
          <Button type="submit" disabled={!draft}>Open dashboard</Button>
        </form>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl space-y-8 px-5 py-10">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="text-3xl font-extrabold">Data management</h1>
        <Button variant="ghost" size="sm" onClick={() => { safeStorage.remove(TOKEN_KEY); setToken(""); }}>Sign out</Button>
      </div>

      {dashboard.isPending && <Skeleton className="h-40 w-full" />}
      {dashboard.isError && !unauthorized && <ErrorState error={dashboard.error} onRetry={() => dashboard.refetch()} />}
      {dashboard.data && (
        <>
          <section className="grid grid-cols-2 gap-3 md:grid-cols-4" aria-label="Totals">
            {Object.entries(dashboard.data.totals).map(([k, v]) => <Stat key={k} label={LABELS[k] ?? k} value={v} />)}
          </section>
          <section className="grid gap-3 md:grid-cols-3">
            <KeyValues title="Destinations by category" data={dashboard.data.pois_by_category} />
            <KeyValues title="Destinations by data origin" data={dashboard.data.pois_by_origin} />
            <KeyValues title="Destinations per provider" data={dashboard.data.source_coverage} />
            <KeyValues title="Match methods" data={dashboard.data.matching.by_method} />
            <KeyValues title="Auto-match confidence" data={dashboard.data.matching.auto_confidence_buckets} />
            <KeyValues title="Missing fields" data={dashboard.data.data_quality} />
            <KeyValues title="Review sentiment status" data={dashboard.data.sentiment_status} />
            <KeyValues title="Live integrations" data={dashboard.data.integrations} />
            <Card className="p-4 text-sm">
              <h3 className="font-bold">Language models</h3>
              <p className="mt-2 text-ink-soft">Primary: {dashboard.data.llm.primary}</p>
              <p className="text-ink-soft">Fallback: {dashboard.data.llm.fallback ?? "none"}</p>
              <p className="text-ink-soft">Ready: {dashboard.data.llm.configured_chain.join(", ") || "none — add API keys"}</p>
            </Card>
          </section>
        </>
      )}

      <section className="space-y-3" aria-labelledby="ingest-h">
        <h2 id="ingest-h" className="text-2xl font-bold">Collect data</h2>
        <p className="max-w-prose text-sm text-ink-soft">
          Collects around Bhubaneswar (30 km). Run Google Places first; OpenStreetMap adds local-language names and
          access tags, Wikipedia adds background text. Yelp and Flickr are optional. "Auto" uses live APIs where
          keys are configured and sample data otherwise.
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <Label htmlFor="mode">Source mode</Label>
            <Select id="mode" value={mode} onChange={(e) => setMode(e.target.value as typeof mode)} className="w-40">
              <option value="auto">Auto</option>
              <option value="mock">Sample data</option>
              <option value="live">Live only</option>
            </Select>
          </div>
          {SOURCES.map((s) => (
            <Button key={s.key} variant="secondary" disabled={ingest.isPending} onClick={() => ingest.mutate(s.key)}>
              {ingest.isPending && ingest.variables === s.key ? "Collecting…" : s.label}
            </Button>
          ))}
          <Button variant="secondary" disabled={curated.isPending} onClick={() => curated.mutate()}>
            {curated.isPending ? "Loading…" : "Load curated facts & festivals"}
          </Button>
        </div>
        {curated.data && <p role="status" className="text-sm">Curated: {curated.data.records_inserted} added, {curated.data.records_updated} updated, {curated.data.records_rejected} not matched.</p>}
        {curated.isError && <ErrorState title="Loading curated data failed" error={curated.error} />}
        {ingest.isError && <ErrorState title="Collection failed" error={ingest.error} />}
        {ingest.data && <p role="status" className="text-sm">Run {ingest.data.status}: {ingest.data.records_inserted} added, {ingest.data.records_updated} updated, {ingest.data.records_rejected} rejected.</p>}
      </section>

      <section className="space-y-3" aria-labelledby="agents-h">
        <h2 id="agents-h" className="text-2xl font-bold">Curation agents</h2>
        <p className="max-w-prose text-sm text-ink-soft">
          Agents run offline and never on a visitor's request. What they find is saved as unverified until
          you approve it below.
        </p>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {AGENTS.map((a) => (
            <Card key={a.key} className="flex flex-col gap-2 p-4">
              <h3 className="font-bold">{a.label}</h3>
              <p className="text-sm text-ink-soft">{a.hint}</p>
              <Button className="mt-auto" size="sm" variant="secondary" disabled={agent.isPending}
                onClick={() => agent.mutate(a.key)}>
                {agent.isPending && agent.variables === a.key ? "Running…" : "Run"}
              </Button>
            </Card>
          ))}
        </div>
        {agent.isError && <ErrorState title="Agent failed" error={agent.error} />}
        {agent.data && (
          <Card role="status" className="space-y-1 p-4 text-sm">
            <p className="num">
              {agent.data.agent}: {agent.data.processed} processed, {agent.data.accepted} accepted,{" "}
              {agent.data.rejected} rejected, {agent.data.errors} errors
            </p>
            {agent.data.notes.slice(0, 8).map((n) => <p key={n} className="text-ink-soft">{n}</p>)}
          </Card>
        )}
      </section>

      <section className="space-y-3" aria-labelledby="facts-h">
        <h2 id="facts-h" className="text-2xl font-bold">Facts waiting for review</h2>
        <p className="max-w-prose text-sm text-ink-soft">
          Only verified facts can hide a place from a visitor (for example "non-Hindus may not enter").
          Open the source and check the quote before you verify.
        </p>
        {facts.isError && <ErrorState error={facts.error} onRetry={() => facts.refetch()} />}
        {facts.data && facts.data.items.length === 0 && <p className="text-sm text-ink-soft">Nothing to review.</p>}
        {facts.data && facts.data.items.length > 0 && (
          <ul className="space-y-2">
            {facts.data.items.map((f) => (
              <li key={f.fact_id}>
                <Card className="flex flex-wrap items-start justify-between gap-3 p-4 text-sm">
                  <div className="min-w-0 max-w-prose space-y-1">
                    <p><span className="font-semibold">{f.display_text}</span>{" "}
                      <span className="text-ink-soft">({f.fact_key.replaceAll("_", " ")}, {f.poi_id})</span></p>
                    {f.evidence && <blockquote className="border-l-2 border-line pl-3 italic text-ink-soft">{f.evidence}</blockquote>}
                    <p className="text-xs text-ink-soft">
                      {f.extracted_by ?? f.source_type} ·{" "}
                      {f.source_url ? <a className="underline" href={f.source_url} target="_blank" rel="noreferrer">{f.source_name ?? "source"}</a> : "no source URL"}
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <Button size="sm" disabled={decide.isPending}
                      onClick={() => decide.mutate({ id: f.fact_id, status: "verified" })}>Verify</Button>
                    <Button size="sm" variant="ghost" disabled={decide.isPending}
                      onClick={() => decide.mutate({ id: f.fact_id, status: "rejected" })}>Reject</Button>
                  </div>
                </Card>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-3" aria-labelledby="runs-h">
        <h2 id="runs-h" className="text-2xl font-bold">Recent runs</h2>
        {runs.isError && <ErrorState error={runs.error} onRetry={() => runs.refetch()} />}
        {runs.data && <RunsTable runs={runs.data.items} />}
      </section>

      <section className="space-y-3" aria-labelledby="match-h">
        <h2 id="match-h" className="text-2xl font-bold">Matches waiting for review</h2>
        {matches.data && matches.data.length === 0 && <p className="text-sm text-ink-soft">No uncertain matches.</p>}
        {matches.data && matches.data.length > 0 && (
          <Card className="overflow-x-auto">
            <table className="num w-full text-left text-sm">
              <thead className="border-b border-line text-ink-soft">
                <tr><th className="p-3">Provider record</th><th className="p-3">Possible match</th><th className="p-3">Score</th><th className="p-3">Why</th></tr>
              </thead>
              <tbody>
                {matches.data.map((m) => (
                  <tr key={m.id} className="border-b border-line/60 last:border-0">
                    <td className="p-3">{String(m.source_payload.name ?? m.external_id)} <span className="text-ink-soft">({m.provider})</span></td>
                    <td className="p-3">{m.candidate_poi_id}</td>
                    <td className="p-3">{m.score.toFixed(2)}</td>
                    <td className="p-3 text-ink-soft">{String(m.score_breakdown.reason ?? `name ${m.score_breakdown.name}, distance ${m.score_breakdown.distance_m} m`)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )}
      </section>
    </div>
  );
}

function RunsTable({ runs }: { runs: IngestionRun[] }) {
  if (runs.length === 0) return <p className="text-sm text-ink-soft">No collection runs yet.</p>;
  return (
    <Card className="overflow-x-auto">
      <table className="num w-full text-left text-sm">
        <thead className="border-b border-line text-ink-soft">
          <tr>
            <th className="p-3">Started</th><th className="p-3">Source</th><th className="p-3">Mode</th><th className="p-3">Status</th>
            <th className="p-3">Fetched</th><th className="p-3">Added</th><th className="p-3">Updated</th><th className="p-3">Rejected</th><th className="p-3">Flagged</th><th className="p-3">Notes</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.run_id} className="border-b border-line/60 align-top last:border-0">
              <td className="p-3">{r.started_at ? new Date(r.started_at).toLocaleString() : "—"}</td>
              <td className="p-3">{r.source}</td>
              <td className="p-3">{r.mode === "mock" ? <Badge tone="warn">sample</Badge> : <Badge tone="info">live</Badge>}</td>
              <td className="p-3"><Badge tone={STATUS_TONE[r.status]}>{r.status}</Badge></td>
              <td className="p-3">{r.records_fetched}</td>
              <td className="p-3">{r.records_inserted}</td>
              <td className="p-3">{r.records_updated}</td>
              <td className="p-3">{r.records_rejected}</td>
              <td className="p-3">{r.records_flagged}</td>
              <td className="max-w-xs p-3 text-ink-soft">
                {r.error_summary ?? Object.entries(r.error_details?.rejection_reasons ?? {}).map(([k, v]) => `${k}: ${v}`).join("; ")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
