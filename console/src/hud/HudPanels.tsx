// The HUD around the chat: the hero (empty conversation), the side panel (laptop and wider)
// and the compact strip (narrow screens). All values come from useHud.

import { useEffect, useRef, type ReactNode } from "react";
import type { SessionInfo } from "../api";
import { cx } from "../components/ui";
import { fmtMs, fmtUsd } from "../format";
import { Reactor } from "./Reactor";
import { PHASE_LABEL, type HudState, type LogKind, type Phase } from "./useHud";

const PHASE_TEXT: Record<Phase, string> = {
  idle: "text-accent-300",
  thinking: "text-accent-300",
  streaming: "text-accent-300",
  tool: "text-accent-300",
  done: "text-accent-300",
  approval: "text-amber-300",
  error: "text-red-300",
  offline: "text-stone-400",
};

export function HudFrame({ title, children, className }: { title: string; children: ReactNode; className?: string }) {
  return (
    <section className={cx("hud-frame p-3", className)} aria-label={title}>
      <h2 className="hud-label mb-2">{title}</h2>
      {children}
    </section>
  );
}

// The one line that says what the agent is doing; also what screen readers hear.
export function PhaseReadout({ hud, className }: { hud: HudState; className?: string }) {
  return (
    <div className={cx("hud-font text-[11px] font-semibold uppercase", PHASE_TEXT[hud.phase], className)} role="status" aria-live="polite">
      {PHASE_LABEL[hud.phase]}
      {hud.activeTool && <span className="normal-case tracking-normal text-stone-100"> · {hud.activeTool}</span>}
    </div>
  );
}

function Row({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1 text-[13px]">
      <dt className="text-stone-400">{label}</dt>
      <dd className="min-w-0 truncate text-right font-mono tabular-nums text-stone-100">
        {value}
        {hint && <span className="ml-1 text-[11px] text-stone-400">{hint}</span>}
      </dd>
    </div>
  );
}

export function Telemetry({ hud, session }: { hud: HudState; session: SessionInfo | null }) {
  const t = hud.telemetry;
  const configured = session?.models[0];
  const provider = t.provider ?? configured?.provider;
  const model = t.model ?? configured?.model;
  return (
    <HudFrame title="Telemetry">
      <dl className="divide-y divide-stone-800">
        <Row label="Provider" value={provider ?? "–"} hint={t.fallback ? "fallback" : undefined} />
        <Row label="Model" value={<span title={model}>{model ?? "–"}</span>} />
        <Row label="Step" value={t.turnSteps} hint={`${t.totalSteps} total`} />
        <Row label="Tokens" value={t.tokens.toLocaleString()} hint={t.estimated ? "est." : undefined} />
        <Row
          label="List price"
          value={t.costUsd == null ? "n/a" : fmtUsd(t.costUsd)}
          hint={t.costMissing ? `${t.costMissing} unpriced` : undefined}
        />
        <Row label="Last model call" value={fmtMs(t.lastCallMs)} />
        <Row label="Last answer" value={fmtMs(t.lastTurnMs)} />
        <Row label="Tool calls" value={t.toolCalls} />
        <Row label="Escalated cards" value={t.escalated} />
      </dl>
      <p className="mt-2 text-[11px] leading-4 text-stone-400">
        This conversation. Escalated cards = approvals with a tiers-v2 notice; the session's taint state is not sent to the
        console, so it isn't shown.
      </p>
    </HudFrame>
  );
}

const LOG_STYLE: Record<LogKind, { mark: string; tone: string }> = {
  user: { mark: "›", tone: "text-stone-100" },
  llm: { mark: "◇", tone: "text-accent-300" },
  tool: { mark: "→", tone: "text-stone-200" },
  ok: { mark: "✓", tone: "text-emerald-300" },
  fail: { mark: "✕", tone: "text-red-300" },
  approval: { mark: "!", tone: "text-amber-300" },
  answer: { mark: "■", tone: "text-accent-200" },
  error: { mark: "✕", tone: "text-red-300" },
};

const clock = (ms: number) => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });

export function EventLog({ hud, className }: { hud: HudState; className?: string }) {
  const box = useRef<HTMLOListElement>(null);
  const count = hud.log.length;
  const lastKey = hud.log.at(-1)?.key;
  useEffect(() => {
    if (box.current && (count || lastKey)) box.current.scrollTop = box.current.scrollHeight;
  }, [count, lastKey]);
  return (
    <HudFrame title="Event log" className={className}>
      {hud.log.length === 0 ? (
        <p className="text-[12px] text-stone-400">No events yet.</p>
      ) : (
        <ol ref={box} className="max-h-72 space-y-0.5 overflow-y-auto pr-1 font-mono text-[11.5px] leading-[18px]" aria-label="Agent events">
          {hud.log.map((e) => (
            <li key={e.key} className="flex gap-2">
              <span className="shrink-0 tabular-nums text-stone-400">{clock(e.at)}</span>
              <span className={cx("w-3 shrink-0 text-center", LOG_STYLE[e.kind].tone)} aria-hidden="true">
                {LOG_STYLE[e.kind].mark}
              </span>
              <span className={cx("min-w-0 break-words", LOG_STYLE[e.kind].tone)}>{e.text}</span>
            </li>
          ))}
        </ol>
      )}
    </HudFrame>
  );
}

export function HudHero({ hud }: { hud: HudState }) {
  return (
    <div className="flex flex-col items-center text-center">
      <Reactor phase={hud.phase} segments={hud.segments} activeTool={hud.activeTool} className="w-[min(340px,56vw)] sm:w-[min(340px,78vw)]" />
      <div className="hud-wordmark mt-2 pl-[0.32em] text-4xl sm:text-5xl">KESTREL</div>
      <p className="hud-font mt-3 text-[11px] uppercase text-stone-300">Hunts on its own · Comes back when called</p>
      <PhaseReadout hud={hud} className="mt-3" />
    </div>
  );
}

// Laptop and wider: rings (once the hero is gone), telemetry, log.
export function HudSide({ hud, session, showReactor }: { hud: HudState; session: SessionInfo | null; showReactor: boolean }) {
  return (
    <aside className="space-y-3">
      {showReactor && (
        <div className="hud-frame flex flex-col items-center p-3">
          <Reactor phase={hud.phase} segments={hud.segments} activeTool={hud.activeTool} burst={hud.burst} className="w-52" />
          <PhaseReadout hud={hud} className="mt-1 text-center" />
        </div>
      )}
      <Telemetry hud={hud} session={session} />
      <EventLog hud={hud} />
    </aside>
  );
}

// Narrow screens: a small ring, the phase, four numbers, and the log behind a disclosure.
export function HudStrip({ hud, showReactor }: { hud: HudState; showReactor: boolean }) {
  const t = hud.telemetry;
  return (
    <div className="border-b border-stone-800 bg-stone-950/85 px-4 py-2">
      <div className="flex items-center gap-3">
        {showReactor && (
          <Reactor phase={hud.phase} segments={hud.segments} activeTool={hud.activeTool} burst={hud.burst} size="compact" className="size-11 shrink-0" />
        )}
        <div className="min-w-0 flex-1">
          <PhaseReadout hud={hud} className="truncate" />
          <div className="mt-0.5 flex flex-wrap gap-x-3 font-mono text-[11px] tabular-nums text-stone-300">
            <span>step {t.turnSteps}</span>
            <span>{t.tokens.toLocaleString()} tok</span>
            <span>{t.costUsd == null ? "price n/a" : fmtUsd(t.costUsd)}</span>
            <span>{fmtMs(t.lastCallMs)}</span>
          </div>
        </div>
      </div>
      <details className="mt-1">
        <summary className="hud-label cursor-pointer select-none py-1">Event log</summary>
        <EventLog hud={hud} className="mt-1" />
      </details>
    </div>
  );
}
