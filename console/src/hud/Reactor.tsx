// The HUD rings around the falcon. Pure SVG; every animation is a CSS rule in hud.css keyed on
// data-phase / data-pose / data-burst, which come from useHud.ts, so the rings and the bird move only
// when the agent's state changes. overflow: hidden keeps every effect inside the reactor's own box.

import { useId, type CSSProperties } from "react";
import { cx } from "../components/ui";
import { FalconPaths } from "./FalconMark";
import { FALCON_POSE, PHASE_LABEL, SEGMENTS, type Phase, type Segment } from "./useHud";

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

// 16 sparks for the answer burst: spread round the circle with a fixed jitter (no randomness, so
// every burst and every screenshot looks the same), each with its own end point and start delay.
const SPARKS = Array.from({ length: 16 }, (_, i) => {
  const deg = i * 22.5 + ((i * 7) % 5) * 3;
  const dist = 130 + ((i * 37) % 50);
  const [x, y] = polar(dist, deg);
  return { x: x - C, y: y - C, delay: (i % 4) * 20, r: i % 3 === 0 ? 5 : 3.5 };
});

const vars = (v: Record<string, string>) => v as CSSProperties;

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
  burst = null,
  size = "hero",
  className,
}: {
  phase: Phase;
  segments: Segment[];
  activeTool: string | null;
  burst?: number | null;
  size?: "hero" | "compact";
  className?: string;
}) {
  const id = useId();
  const pose = FALCON_POSE[phase];
  const label = PHASE_LABEL[phase] + (activeTool ? `: ${activeTool}` : "");
  const activeIdx = segments.findLastIndex((s) => s.state === "active");
  const mid = activeIdx >= 0 ? activeIdx * SPAN + SPAN / 2 : 0;
  const lower = mid > 90 && mid < 270;

  // The dive: toward the lit segment, about two thirds of the way to the ring, tilted into it.
  const [tx, ty] = polar(70, mid);
  const tilt = ((mid + 180) % 360) - 180;
  const dive = vars({ "--dx": `${(tx - C).toFixed(1)}px`, "--dy": `${(ty - C).toFixed(1)}px`, "--dr": `${(tilt * 0.4).toFixed(1)}deg` });

  return (
    <svg
      viewBox="-30 -30 460 460"
      className={cx("reactor", className)}
      data-phase={phase}
      data-pose={pose}
      data-burst={burst != null ? "" : undefined}
      role="img"
      aria-label={`Kestrel status: ${label}`}
    >
      <defs>
        <radialGradient id={`${id}core`}>
          <stop offset="0%" stopColor="currentColor" stopOpacity={0.28} />
          <stop offset="70%" stopColor="currentColor" stopOpacity={0.06} />
          <stop offset="100%" stopColor="currentColor" stopOpacity={0} />
        </radialGradient>
        <radialGradient id={`${id}burst`}>
          <stop offset="0%" stopColor="var(--hud-text)" stopOpacity={0.6} />
          <stop offset="45%" stopColor="currentColor" stopOpacity={0.35} />
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

      {burst != null && (
        // Played once per answer (useHud's burstReducer); the key restarts it for the next one.
        <g key={burst} className="burst" aria-hidden="true">
          <circle className="burst-glow" cx={C} cy={C} r={170} fill={`url(#${id}burst)`} />
          <circle className="shock" cx={C} cy={C} r={112} fill="none" stroke="var(--hud-text)" strokeWidth={5} />
          {SPARKS.map((s, i) => (
            <circle
              key={i}
              className="spark"
              cx={C}
              cy={C}
              r={s.r}
              fill="var(--hud-text)"
              style={vars({ "--sx": `${s.x.toFixed(1)}px`, "--sy": `${s.y.toFixed(1)}px`, animationDelay: `${s.delay}ms` })}
            />
          ))}
        </g>
      )}

      {phase === "waking" && (
        // a soft ring once the falcon has landed (wake fly-in); hidden under reduced motion
        <circle className="wake-ring" cx={C} cy={C} r={112} fill="none" stroke="currentColor" strokeWidth={3} aria-hidden="true" />
      )}

      {/* flight = lift, dive, swoop, shake, arrive; bob = the idle float; the wings move inside the nested svg.
          A new tool call remounts the flight group, so each one gets its own dive. */}
      <g key={pose === "dive" ? `dive-${segments.length}` : "flight"} className="falcon-flight" style={dive}>
        <g className="falcon-bob">
          <svg x={C - 70} y={C - 72} width={140} height={140} viewBox="0 0 100 100" overflow="visible">
            <FalconPaths />
          </svg>
        </g>
      </g>
      <circle className="flash" cx={C} cy={C} r={200} fill="var(--hud-red)" />
    </svg>
  );
}
