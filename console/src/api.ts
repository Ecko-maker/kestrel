// Types and fetch helpers for the Kestrel backend (src/kestrel/web/app.py).

export type Risk = "safe" | "confirm" | "forbidden";

export interface SessionInfo {
  ok: boolean;
  demo?: boolean;
  demo_notice?: string | null;
  demo_prompts?: string[];
  chat_available: boolean;
  problem: string | null;
  models: { provider: string; model: string }[];
  tools: { name: string; risk: Risk; server: string | null }[];
}

export interface TraceRow {
  trace_id: string;
  start_time: number; // epoch seconds
  duration_ms: number | null;
  wait_ms: number | null;
  latency_ms: number; // excludes time waiting for approvals
  status: "ok" | "error";
  stop_reason: string | null;
  steps: number | null;
  user_message: string | null; // null when content recording is off
  final_answer: string | null;
  tokens: number;
  input_tokens: number;
  output_tokens: number;
  tokens_estimated: number;
  cost_usd: number | null;
  list_price_usd: number | null;
  providers: string[];
  fallback: number;
  rating: "good" | "bad" | null;
  rating_note: string | null;
  session_id: string | null;
}

export interface Span {
  span_id: string;
  trace_id: string;
  parent_id: string | null;
  name: "agent_run" | "llm_call" | "tool_call" | "approval" | string;
  start_time: number;
  end_time: number | null;
  duration_ms: number | null;
  status: "ok" | "error";
  error: string | null;
  attributes: Record<string, unknown>;
}

export interface TraceDetail extends TraceRow {
  spans: Span[];
}

export interface SeriesPoint {
  trace_id: string;
  start_time: number;
  latency_ms: number;
  tokens: number;
  list_price_usd: number | null;
  status: string;
  rating: string | null;
}

export interface Stats {
  traces: number;
  requests_today?: number;
  first?: number;
  last?: number;
  latency_p50_ms?: number | null;
  latency_p95_ms?: number | null;
  llm_latency_ms?: Record<string, [number | null, number | null, number]>;
  tokens_total?: number;
  tokens_per_request?: number;
  steps_per_request?: number;
  cost_usd_total?: number;
  list_price_usd_total?: number | null;
  list_price_usd_per_request?: number | null;
  list_price_usd_per_conversation?: number | null;
  list_price_coverage?: number;
  tool_calls?: Record<string, number>;
  tool_error_rate?: number;
  approvals?: Record<string, number>;
  error_rate?: number;
  fallback_rate?: number;
  rated?: number;
  good_share?: number | null;
  providers?: Record<string, number>;
  series?: SeriesPoint[];
}

export class AuthError extends Error {}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (response.status === 401) throw new AuthError("Open the link printed by `kestrel web`.");
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

/** In dev (Vite), the token arrives in the URL: trade it for the session cookie, then hide it. */
export async function bootstrapAuth(): Promise<void> {
  const url = new URL(window.location.href);
  const token = url.searchParams.get("token");
  if (!token) return;
  await fetch(`/api/session?token=${encodeURIComponent(token)}`, { credentials: "same-origin" });
  url.searchParams.delete("token");
  window.history.replaceState(null, "", url.pathname + url.search + url.hash);
}

export const rateTrace = (traceId: string, rating: "good" | "bad", note = "") =>
  api<{ ok: boolean }>(`/api/traces/${traceId}/rating`, { method: "POST", body: JSON.stringify({ rating, note }) });
