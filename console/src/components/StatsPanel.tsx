import type { Stats } from "../api";
import { fmtMs, fmtPct, fmtUsd } from "../format";
import { Badge, Tile } from "./ui";

export function StatsPanel({ stats }: { stats: Stats | null }) {
  const s = stats;
  return (
    <aside className="space-y-3">
      <h2 className="px-1 text-xs font-semibold uppercase tracking-wider text-stone-500 dark:text-stone-400">Live</h2>
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-1">
        <Tile label="Requests today" value={s?.requests_today ?? "–"} hint={s ? `${s.traces} all time` : undefined} />
        <Tile label="p50 latency" value={fmtMs(s?.latency_p50_ms)} hint={s?.latency_p95_ms != null ? `p95 ${fmtMs(s.latency_p95_ms)}` : "excludes approval time"} />
        <Tile
          label="List price / conversation"
          value={fmtUsd(s?.list_price_usd_per_conversation)}
          hint={s?.cost_usd_total != null ? `actually paid: ${fmtUsd(s.cost_usd_total)}` : undefined}
        />
        <Tile label="Rated good" value={fmtPct(s?.good_share)} hint={s ? `${s.rated ?? 0} rated` : undefined} />
      </div>
      <div className="rounded-xl bg-white px-4 py-3 ring-1 ring-stone-200/80 dark:bg-stone-900 dark:ring-stone-800">
        <div className="text-[11px] font-medium uppercase tracking-wider text-stone-500 dark:text-stone-400">Answered by</div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {s?.providers && Object.keys(s.providers).length ? (
            Object.entries(s.providers).map(([p, n]) => (
              <Badge key={p} tone="accent">
                {p} <span className="opacity-60">{n}</span>
              </Badge>
            ))
          ) : (
            <span className="text-sm text-stone-400">No answers yet</span>
          )}
        </div>
      </div>
    </aside>
  );
}
