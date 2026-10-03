import type { ReactNode } from "react";
import { ChartColumn } from "lucide-react";
import type { Stats } from "../api";
import { HBars, TimeChart } from "../components/Charts";
import { Card, Empty, Tile } from "../components/ui";
import { fmtInt, fmtMs, fmtPct, fmtUsd } from "../format";

function Panel({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <Card className="p-4">
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <h2 className="font-medium">{title}</h2>
        {hint && <span className="text-xs text-stone-500">{hint}</span>}
      </div>
      {children}
    </Card>
  );
}

export function StatsPage({ stats }: { stats: Stats | null }) {
  if (stats && stats.traces === 0) {
    return (
      <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
        <h1 className="text-2xl font-semibold tracking-tight">Stats</h1>
        <Empty icon={<ChartColumn className="size-6" />} title="No data yet">Chat with Kestrel and the numbers fill in here.</Empty>
      </div>
    );
  }
  const series = stats?.series ?? [];
  const priced = series.filter((p) => p.list_price_usd != null);

  return (
    <div className="mx-auto max-w-6xl space-y-6 px-4 py-8 sm:px-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Stats</h1>
        <p className="mt-1 text-sm text-stone-500 dark:text-stone-400">
          The baseline later phases have to beat. Latency excludes time spent waiting on your approvals.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Tile label="Requests" value={fmtInt(stats?.traces)} hint={`${stats?.requests_today ?? 0} today`} />
        <Tile label="Latency p50 / p95" value={fmtMs(stats?.latency_p50_ms)} hint={`p95 ${fmtMs(stats?.latency_p95_ms)}`} />
        <Tile label="Tokens / request" value={fmtInt(stats?.tokens_per_request)} hint={`${fmtInt(stats?.tokens_total)} total`} />
        <Tile label="List price / request" value={fmtUsd(stats?.list_price_usd_per_request)} hint={`actually paid ${fmtUsd(stats?.cost_usd_total)}`} />
        <Tile label="Rated good" value={fmtPct(stats?.good_share)} hint={`${stats?.rated ?? 0} rated`} />
        <Tile label="Error rate" value={fmtPct(stats?.error_rate)} hint={`tool errors ${fmtPct(stats?.tool_error_rate)}`} />
        <Tile label="Fallback rate" value={fmtPct(stats?.fallback_rate)} hint="switched provider" />
        <Tile label="Steps / request" value={stats?.steps_per_request?.toFixed(1) ?? "–"} hint="model calls" />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Latency" hint="per request">
          <TimeChart points={series.map((p) => ({ x: p.start_time, y: p.latency_ms }))} format={fmtMs} />
        </Panel>
        <Panel title="Tokens" hint="per request">
          <TimeChart kind="bars" points={series.map((p) => ({ x: p.start_time, y: p.tokens }))} format={(v) => (v >= 1000 ? `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k` : String(Math.round(v)))} />
        </Panel>
        <Panel title="List price" hint={priced.length < series.length ? `${priced.length} of ${series.length} requests priced` : "per request"}>
          <TimeChart
            points={priced.map((p) => ({ x: p.start_time, y: p.list_price_usd ?? 0 }))}
            format={fmtUsd}
            empty="No list prices yet: local models have no paid reference price"
          />
        </Panel>
        <Panel title="Tool usage">
          <HBars data={stats?.tool_calls ?? {}} />
          {stats?.approvals && Object.keys(stats.approvals).length > 0 && (
            <div className="mt-5 border-t border-stone-100 pt-4 dark:border-stone-800">
              <div className="mb-3 text-sm font-medium">Approval decisions</div>
              <HBars data={stats.approvals} tone="amber" />
            </div>
          )}
        </Panel>
      </div>

      {stats?.llm_latency_ms && Object.keys(stats.llm_latency_ms).length > 0 && (
        <Panel title="Model latency by provider">
          <div className="grid gap-3 sm:grid-cols-3">
            {Object.entries(stats.llm_latency_ms).map(([provider, [p50, p95, n]]) => (
              <div key={provider} className="rounded-lg bg-stone-50 px-3 py-2 dark:bg-stone-950/60">
                <div className="font-medium">{provider}</div>
                <div className="text-sm tabular-nums text-stone-500">p50 {fmtMs(p50)} · p95 {fmtMs(p95)} · {n} calls</div>
              </div>
            ))}
          </div>
        </Panel>
      )}
    </div>
  );
}
