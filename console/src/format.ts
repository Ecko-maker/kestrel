export function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return "–";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
}

export function fmtUsd(x: number | null | undefined): string {
  if (x == null) return "n/a";
  if (x === 0) return "$0";
  if (x < 0.01) return `$${x.toFixed(4)}`;
  return `$${x.toFixed(2)}`;
}

export const fmtInt = (n: number | null | undefined) => (n == null ? "–" : Math.round(n).toLocaleString());

export const fmtPct = (x: number | null | undefined) => (x == null ? "–" : `${Math.round(x * 100)}%`);

export function fmtTime(epochSeconds: number): string {
  const d = new Date(epochSeconds * 1000);
  const today = new Date();
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === today.toDateString()
    ? time
    : `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
}

export function prettyArgs(json: string): string {
  try {
    const args = JSON.parse(json || "{}") as Record<string, unknown>;
    return Object.entries(args)
      .map(([k, v]) => {
        const s = typeof v === "string" ? v : JSON.stringify(v);
        return `${k}=${JSON.stringify(s.length > 60 ? s.slice(0, 57) + "…" : s)}`;
      })
      .join(", ");
  } catch {
    return json;
  }
}
