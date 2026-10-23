// What the HUD shows, derived only from the chat state the console already has (useChat's turns).
// No new server events: every value here comes from step_started, text_delta, llm_call, tool_call,
// tool_result, approval_required/resolved, answer, error and done.
// This file is the one place that maps events to HUD states (phase, falcon pose, answer burst);
// the components only draw what it returns. Tested by useHud.test.mjs (no runtime imports but react).

import { useEffect, useMemo, useReducer } from "react";
import type { ConnectionState, Turn } from "../useChat";

// Note: llm_call arrives when the model has *finished* (it carries duration and tokens), so
// "thinking" is the gap between step_started (or a tool result) and the next llm_call.
// "waking": the wake word was just heard (server event `wake`); shown only over idle/done, never over work.
export type Phase = "idle" | "thinking" | "streaming" | "tool" | "approval" | "error" | "done" | "offline" | "waking";

export type SegmentState = "active" | "ok" | "failed";
export interface Segment {
  name: string;
  state: SegmentState;
}

export interface Telemetry {
  provider?: string;
  model?: string;
  fallback: boolean;
  turnSteps: number;
  totalSteps: number;
  tokens: number;
  estimated: boolean;
  costUsd: number | null; // list price, summed over answered turns that have one
  costMissing: number; // answered turns without a list price (e.g. demo, Ollama)
  lastCallMs: number | null;
  lastTurnMs: number | null;
  toolCalls: number;
  escalated: number; // approval cards that came with a tiers-v2 notice
}

export type LogKind = "user" | "llm" | "tool" | "ok" | "fail" | "approval" | "answer" | "error";
export interface LogEntry {
  key: string;
  at: number;
  kind: LogKind;
  text: string;
}

export interface HudState {
  phase: Phase;
  pose: FalconPose;
  burst: number | null; // id of the answer burst playing now, if any
  greeting: string | null; // the spoken greeting, while the wake fly-in plays
  activeTool: string | null;
  segments: Segment[];
  telemetry: Telemetry;
  log: LogEntry[];
}

export const SEGMENTS = 8; // the tool ring has 8 slots; a turn's 9th call reuses slot 1

export const PHASE_LABEL: Record<Phase, string> = {
  idle: "Standing by",
  thinking: "Thinking",
  streaming: "Responding",
  tool: "Running tool",
  approval: "Approval needed",
  error: "Error",
  done: "Done",
  offline: "Offline",
  waking: "Hello",
};

export function phaseOf(turns: Turn[], connection: ConnectionState): { phase: Phase; activeTool: string | null } {
  const last = turns[turns.length - 1];
  const none = { activeTool: null };
  if (connection !== "open" && (!last || last.done)) return { phase: "offline", ...none };
  if (!last) return { phase: "idle", ...none };
  const tools = last.steps.flatMap((s) => s.tools);
  if (last.error || last.steps.some((s) => s.llm && !s.llm.ok) || last.answer?.stopReason === "error") {
    return { phase: "error", ...none };
  }
  if (tools.some((t) => t.approvals.some((a) => a.status === "pending"))) {
    return { phase: "approval", activeTool: tools.find((t) => t.approvals.some((a) => a.status === "pending"))?.name ?? null };
  }
  if (last.done) return { phase: "done", ...none };
  const running = tools.filter((t) => !t.result);
  if (running.length) return { phase: "tool", activeTool: running[running.length - 1].name };
  const step = last.steps[last.steps.length - 1];
  if (step && !step.llm && step.text) return { phase: "streaming", ...none };
  return { phase: "thinking", ...none };
}

// The falcon tells the tagline: perched while idle, flapping while it thinks, diving at the lit
// segment for a tool, hovering while it waits for you, swooping back with the answer.
export type FalconPose = "perch" | "flap" | "dive" | "hover" | "return" | "shake" | "rest" | "arrive";

export const FALCON_POSE: Record<Phase, FalconPose> = {
  idle: "perch",
  thinking: "flap",
  streaming: "flap",
  tool: "dive",
  approval: "hover",
  done: "return",
  error: "shake",
  offline: "rest",
  waking: "arrive", // "Comes back when called": flies in from outside the rings, wings open
};

// ---- Answer burst ----
// Only the `answer` event sets turn.answer (text_delta never does), so this key changes exactly when
// an answer arrives. Failed turns get an error answer too; they get no burst.
export function answerKey(turns: Turn[]): string | null {
  for (let i = turns.length - 1; i >= 0; i--) {
    const t = turns[i];
    if (t.answer) return t.answer.stopReason === "error" || t.error ? null : t.id;
  }
  return null;
}

// Every burst animation ends within this (hud.css). One burst at a time, so at most one glow per
// 0.9 s: well under WCAG 2.3.1's three flashes per second.
export const BURST_MS = 900;

export interface BurstState {
  seen: string | null; // the last answer considered, played or not
  playing: number | null; // id of the burst on screen
  count: number;
}

export type BurstAction = { type: "answer"; key: string | null; reducedMotion: boolean } | { type: "end"; id: number };

export const burstStart = (key: string | null): BurstState => ({ seen: key, playing: null, count: 0 });

export function burstReducer(state: BurstState, action: BurstAction): BurstState {
  if (action.type === "end") return state.playing === action.id ? { ...state, playing: null } : state;
  if (action.key === null || action.key === state.seen) return state;
  // An answer that lands while a burst plays (or under reduced motion) is marked seen and never plays.
  if (state.playing !== null || action.reducedMotion) return { ...state, seen: action.key };
  const id = state.count + 1;
  return { seen: action.key, playing: id, count: id };
}

// One play per new key, ms long, never two at once; keys already present on mount don't play.
// Used for the answer burst (suppressed under reduced motion) and the wake fly-in (not suppressed:
// under reduced motion the falcon stays still, but the colour and greeting still show).
function useOneShot(key: string | null, suppressed: boolean, ms: number): number | null {
  const [state, dispatch] = useReducer(burstReducer, key, burstStart);
  useEffect(() => dispatch({ type: "answer", key, reducedMotion: suppressed }), [key, suppressed]);
  useEffect(() => {
    if (state.playing === null) return;
    const id = state.playing;
    const timer = setTimeout(() => dispatch({ type: "end", id }), ms);
    return () => clearTimeout(timer);
  }, [state.playing, ms]);
  return state.playing;
}

// ---- Wake ----
// The wake fly-in: from outside the rings to the centre, wings open, a soft ring, then idle.
export const WAKE_MS = 1600;

// A wake shows only when Kestrel isn't busy: it never covers thinking, a tool, an approval or an error.
export function withWake(phase: Phase, waking: boolean): Phase {
  return waking && (phase === "idle" || phase === "done") ? "waking" : phase;
}

export function segmentsOf(turn: Turn | undefined): Segment[] {
  if (!turn) return [];
  return turn.steps
    .flatMap((s) => s.tools)
    .slice(-SEGMENTS)
    .map((t) => ({
      name: t.name,
      state: !t.result ? "active" : t.result.ok && t.result.ran ? "ok" : "failed",
    }));
}

export function telemetryOf(turns: Turn[]): Telemetry {
  const steps = turns.flatMap((t) => t.steps);
  const calls = steps.filter((s) => s.llm?.ok);
  const lastCall = steps.filter((s) => s.llm).at(-1)?.llm;
  const answered = turns.filter((t) => t.answer);
  const priced = answered.filter((t) => t.answer!.listPriceUsd != null);
  const approvals = steps.flatMap((s) => s.tools.flatMap((t) => t.approvals));
  return {
    provider: lastCall?.provider,
    model: lastCall?.model,
    fallback: steps.some((s) => s.llm?.fallback),
    turnSteps: turns.at(-1)?.steps.length ?? 0,
    totalSteps: steps.length,
    tokens: calls.reduce((n, s) => n + (s.llm!.inputTokens ?? 0) + (s.llm!.outputTokens ?? 0), 0),
    estimated: calls.some((s) => s.llm!.estimated),
    costUsd: priced.length ? priced.reduce((n, t) => n + t.answer!.listPriceUsd!, 0) : null,
    costMissing: answered.length - priced.length,
    lastCallMs: lastCall?.durationMs ?? null,
    lastTurnMs: answered.at(-1)?.answer?.durationMs ?? null,
    toolCalls: steps.reduce((n, s) => n + s.tools.length, 0),
    escalated: approvals.filter((a) => a.notice && a.notice.reasons.length > 0).length,
  };
}

const clip = (s: string, n = 48) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

export function logOf(turns: Turn[], limit = 60): LogEntry[] {
  const out: LogEntry[] = [];
  for (const turn of turns) {
    out.push({ key: `${turn.id}:u`, at: turn.startedAt, kind: "user", text: clip(turn.user) });
    for (const step of turn.steps) {
      const llm = step.llm;
      if (llm) {
        const tokens = llm.inputTokens != null ? ` · ${(llm.inputTokens + (llm.outputTokens ?? 0)).toLocaleString()} tok` : "";
        out.push({
          key: `${turn.id}:${step.step}:llm`,
          at: step.startedAt,
          kind: llm.ok ? "llm" : "fail",
          text: llm.ok ? `${llm.provider ?? "model"} ${fmtShort(llm.durationMs)}${tokens}` : `model failed: ${clip(llm.error ?? "error")}`,
        });
      }
      for (const tool of step.tools) {
        out.push({ key: `${tool.callId}:c`, at: tool.startedAt, kind: "tool", text: `${tool.name}()` });
        for (const a of tool.approvals) {
          out.push({
            key: `${a.id}:a`,
            at: a.receivedAt,
            kind: "approval",
            text: a.status === "pending" ? `approval needed: ${a.tool}` : `${a.status}: ${a.tool}`,
          });
        }
        const r = tool.result;
        if (r) {
          out.push({
            key: `${tool.callId}:r`,
            at: tool.startedAt,
            kind: r.ok && r.ran ? "ok" : "fail",
            text: r.ran ? `${tool.name} ${r.ok ? "ok" : "error"} ${fmtShort(r.durationMs)}` : `${tool.name} ${r.decision || "not run"}`,
          });
        }
      }
    }
    if (turn.error) out.push({ key: `${turn.id}:e`, at: turn.startedAt, kind: "error", text: clip(turn.error, 80) });
    else if (turn.answer) {
      const at = turn.startedAt + (turn.answer.durationMs ?? 0);
      out.push({ key: `${turn.id}:ans`, at, kind: "answer", text: `answer · ${turn.answer.steps} steps · ${fmtShort(turn.answer.durationMs)}` });
    }
  }
  return out.slice(-limit);
}

function fmtShort(ms: number | null | undefined): string {
  if (ms == null) return "";
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`;
}

export function useHud(
  turns: Turn[],
  connection: ConnectionState,
  reducedMotion: boolean,
  wake: { key: string; greeting: string } | null = null,
): HudState {
  const burst = useOneShot(answerKey(turns), reducedMotion, BURST_MS);
  const waking = useOneShot(wake?.key ?? null, false, WAKE_MS) !== null;
  const base = useMemo(() => {
    const { phase, activeTool } = phaseOf(turns, connection);
    return {
      phase,
      pose: FALCON_POSE[phase],
      activeTool,
      segments: segmentsOf(turns.at(-1)),
      telemetry: telemetryOf(turns),
      log: logOf(turns),
    };
  }, [turns, connection]);
  return useMemo(() => {
    const phase = withWake(base.phase, waking);
    const greeting = phase === "waking" ? (wake?.greeting ?? null) : null;
    return { ...base, phase, pose: FALCON_POSE[phase], burst, greeting };
  }, [base, burst, waking, wake?.greeting]);
}
