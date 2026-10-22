// The HUD rings around the falcon. Pure SVG; every animation is a CSS rule in hud.css keyed on
// data-phase, so the rings move only when the agent's state changes (see useHud.ts).

import { useId } from "react";
import { cx } from "../components/ui";
import { FalconPaths } from "./FalconMark";
import { PHASE_LABEL, SEGMENTS, type Phase, type Segment } from "./useHud";

const C = 200;
const SPAN = 360 / SEGMENTS;
const GAP = 5;

function polar(r: number, deg: number): [number, number] {
  const a = ((deg - 90) * Math.PI) / 180;
  return [C + r * Math.cos(a), C + r * Math.sin(a)];
}

// An arc from start to end degrees (0 = top, clockwise). reverse draws it the other way round,
// so text on the lower half of the ring reads left to right instead of upside down.
function arc(r: number, start: number, end: number, reverse = false): string {
  const [x1, y1] = polar(r, reverse ? end : start);
  const [x2, y2] = polar(r, reverse ? start : end);
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${r} ${r} 0 0 ${reverse ? 0 : 1} ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}

const TICKS = Array.from({ length: 72 }, (_, i) => {
  const deg = i * 5;
  const [x1, y1] = polar(i % 6 === 0 ? 184 : 188, deg);
  const [x2, y2] = polar(193, deg);
  return `M${x1.toFixed(1)} ${y1.toFixed(1)}L${x2.toFixed(1)} ${y2.toFixed(1)}`;
}).join("");

const SEG_PATHS = Array.from({ length: SEGMENTS }, (_, i) => arc(156, i * SPAN + GAP / 2, (i + 1) * SPAN - GAP / 2));

function segClass(state: Segment["state"] | undefined): { stroke: string; opacity: number } {
  if (state === "active") return { stroke: "currentColor", opacity: 1 };
  if (state === "ok") return { stroke: "currentColor", opacity: 0.55 };
  if (state === "failed") return { stroke: "var(--hud-red)", opacity: 0.7 };
  return { stroke: "currentColor", opacity: 0.12 };
}

export function Reactor({
  phase,
  segments,
  activeTool,
  size = "hero",
  className,
}: {
  phase: Phase;
  segments: Segment[];
  activeTool: string | null;
  size?: "hero" | "compact";
  className?: string;
}) {
  const id = useId();
  const label = PHASE_LABEL[phase] + (activeTool ? `: ${activeTool}` : "");
  const activeIdx = segments.findLastIndex((s) => s.state === "active");
  const mid = activeIdx >= 0 ? activeIdx * SPAN + SPAN / 2 : 0;
  const lower = mid > 90 && mid < 270;

  return (
    <svg viewBox="-30 -30 460 460" className={cx("reactor", className)} data-phase={phase} role="img" aria-label={`Kestrel status: ${label}`}>
      <defs>
        <radialGradient id={`${id}core`}>
          <stop offset="0%" stopColor="currentColor" stopOpacity={0.28} />
          <stop offset="70%" stopColor="currentColor" stopOpacity={0.06} />
          <stop offset="100%" stopColor="currentColor" stopOpacity={0} />
        </radialGradient>
        {activeIdx >= 0 && size === "hero" && (
          // the label runs along 160 degrees centred on the lit segment, so long tool names fit
          <path id={`${id}label`} d={arc(lower ? 222 : 206, mid - 80, mid + 80, lower)} />
        )}
      </defs>

      <g className="rings" fill="none" stroke="currentColor">
        <circle cx={C} cy={C} r={198} strokeOpacity={0.18} strokeWidth={1} />
        <path className="breath" d={TICKS} strokeWidth={1.4} strokeOpacity={0.7} />
        <circle className="spin" cx={C} cy={C} r={176} strokeWidth={2} strokeOpacity={0.6} strokeDasharray="70 24 10 24" />
        {SEG_PATHS.map((d, i) => {
          const s = segClass(segments[i]?.state);
          return (
            <path
              key={i}
              className="seg"
              d={d}
              data-state={segments[i]?.state ?? "off"}
              stroke={s.stroke}
              strokeOpacity={s.opacity}
              strokeWidth={8}
              strokeLinecap="butt"
            >
              {segments[i] && <title>{segments[i].name}</title>}
            </path>
          );
        })}
        <circle className="spin-rev" cx={C} cy={C} r={134} strokeWidth={1.5} strokeOpacity={0.5} strokeDasharray="2 7" />
        <circle className="breath glow" cx={C} cy={C} r={114} strokeWidth={2.5} />
      </g>
      <circle cx={C} cy={C} r={112} fill={`url(#${id}core)`} />

      {activeIdx >= 0 && size === "hero" && (
        <text className="hud-font" fontSize={20} fontWeight={600} fill="currentColor" letterSpacing="0.08em">
          <textPath href={`#${id}label`} startOffset="50%" textAnchor="middle">
            {segments[activeIdx].name}
          </textPath>
        </text>
      )}

      <g transform={`translate(${C - 70} ${C - 72}) scale(1.4)`}>
        <FalconPaths />
      </g>
      <circle className="flash" cx={C} cy={C} r={200} fill="var(--hud-red)" />
    </svg>
  );
}
