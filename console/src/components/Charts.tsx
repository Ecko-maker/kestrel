// Small dependency-free SVG charts. They scale to their container's width.

import { useState } from "react";
import { cx } from "./ui";

interface Point {
  x: number; // epoch seconds
  y: number;
  label?: string;
}

const W = 640;
const H = 200;
const PAD = { l: 48, r: 12, t: 12, b: 24 };

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

export function TimeChart({
  points,
  format,
  kind = "line",
  empty = "No data yet",
}: {
  points: Point[];
  format: (v: number) => string;
  kind?: "line" | "bars";
  empty?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  if (points.length === 0) {
    return <div className="flex h-[200px] items-center justify-center text-sm text-stone-400">{empty}</div>;
  }
  const max = niceMax(Math.max(...points.map((p) => p.y)));
  const innerW = W - PAD.l - PAD.r;
  const innerH = H - PAD.t - PAD.b;
  // Requests are spaced evenly by order: gaps between sessions would otherwise squash everything.
  const xs = points.map((_, i) => PAD.l + (points.length === 1 ? innerW / 2 : (i / (points.length - 1)) * innerW));
  const ys = points.map((p) => PAD.t + innerH - (p.y / max) * innerH);
  const ticks = [0, 0.5, 1].map((f) => f * max);
  const barW = Math.max(2, Math.min(18, (innerW / points.length) * 0.6));
  const h = hover != null ? points[hover] : null;

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => {
          const y = PAD.t + innerH - (t / max) * innerH;
          return (
            <g key={t}>
              <line x1={PAD.l} x2={W - PAD.r} y1={y} y2={y} className="stroke-stone-200 dark:stroke-stone-800" strokeDasharray={t ? "3 4" : undefined} />
              <text x={PAD.l - 8} y={y + 4} textAnchor="end" className="fill-stone-400 text-[11px] tabular-nums">
                {format(t)}
              </text>
            </g>
          );
        })}
        {kind === "line" ? (
          <>
            <path
              d={`M${xs[0]},${PAD.t + innerH} ` + xs.map((x, i) => `L${x},${ys[i]}`).join(" ") + ` L${xs[xs.length - 1]},${PAD.t + innerH} Z`}
              className="fill-accent-500/10"
            />
            <polyline points={xs.map((x, i) => `${x},${ys[i]}`).join(" ")} fill="none" strokeWidth={2} strokeLinejoin="round" className="stroke-accent-500" />
            {xs.map((x, i) => (
              <circle key={i} cx={x} cy={ys[i]} r={hover === i ? 4.5 : 2.5} className="fill-accent-600 stroke-white dark:stroke-stone-900" strokeWidth={1.5} />
            ))}
          </>
        ) : (
          xs.map((x, i) => (
            <rect
              key={i}
              x={x - barW / 2}
              y={ys[i]}
              width={barW}
              height={PAD.t + innerH - ys[i]}
              rx={2}
              className={cx(hover === i ? "fill-accent-600" : "fill-accent-400/80")}
            />
          ))
        )}
        {xs.map((x, i) => (
          <rect key={`hit${i}`} x={x - innerW / points.length / 2} y={PAD.t} width={innerW / points.length} height={innerH} fill="transparent" onMouseEnter={() => setHover(i)} />
        ))}
        <text x={PAD.l} y={H - 6} className="fill-stone-400 text-[11px]">
          {new Date(points[0].x * 1000).toLocaleDateString([], { month: "short", day: "numeric" })}
        </text>
        <text x={W - PAD.r} y={H - 6} textAnchor="end" className="fill-stone-400 text-[11px]">
          {new Date(points[points.length - 1].x * 1000).toLocaleDateString([], { month: "short", day: "numeric" })}
        </text>
      </svg>
      {h && (
        <div className="pointer-events-none absolute right-2 top-1 rounded-md bg-stone-900/90 px-2 py-1 text-xs text-white shadow dark:bg-stone-100/95 dark:text-stone-900">
          <span className="font-semibold tabular-nums">{format(h.y)}</span>
          <span className="opacity-70"> · {new Date(h.x * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
          {h.label && <div className="max-w-56 truncate opacity-70">{h.label}</div>}
        </div>
      )}
    </div>
  );
}

export function HBars({ data, tone = "accent" }: { data: Record<string, number>; tone?: "accent" | "violet" | "amber" }) {
  const entries = Object.entries(data).toSorted((a, b) => b[1] - a[1]);
  if (!entries.length) return <div className="py-6 text-center text-sm text-stone-400">Nothing yet</div>;
  const max = Math.max(...entries.map(([, v]) => v));
  const fill = { accent: "bg-accent-500", violet: "bg-violet-500", amber: "bg-amber-500" }[tone];
  return (
    <ul className="space-y-2">
      {entries.map(([k, v]) => (
        <li key={k} className="grid grid-cols-[minmax(0,10rem)_1fr_2.5rem] items-center gap-3 text-sm">
          <span className="truncate font-mono text-[12.5px] text-stone-600 dark:text-stone-300" title={k}>
            {k}
          </span>
          <span className="h-2 overflow-hidden rounded-full bg-stone-100 dark:bg-stone-800">
            <span className={cx("block h-full rounded-full", fill)} style={{ width: `${(v / max) * 100}%` }} />
          </span>
          <span className="text-right tabular-nums text-stone-500">{v}</span>
        </li>
      ))}
    </ul>
  );
}
