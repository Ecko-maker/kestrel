import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, ChartGantt, Search, ThumbsDown, ThumbsUp } from "lucide-react";
import { api, rateTrace, type Span, type TraceDetail, type TraceRow } from "../api";
import { fmtInt, fmtMs, fmtTime, fmtUsd } from "../format";
import { Markdown } from "../components/Markdown";
import { Badge, Card, Empty, Spinner, cx, riskTone } from "../components/ui";

export function TracesPage({ traceId, navigate }: { traceId?: string; navigate: (hash: string) => void }) {
  return traceId ? <TraceView id={traceId} back={() => navigate("#/traces")} /> : <TraceList open={(id) => navigate(`#/traces/${id}`)} />;
}

function TraceList({ open }: { open: (id: string) => void }) {
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<TraceRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const id = setTimeout(() => {
      api<TraceRow[]>(`/api/traces?limit=200${query ? `&q=${encodeURIComponent(query)}` : ""}`)
        .then(setRows)
        .catch((e) => setError(String(e.message ?? e)));
    }, 200);
    return () => clearTimeout(id);
  }, [query]);

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Traces</h1>
          <p className="mt-1 text-sm text-stone-500 dark:text-stone-400">Every request Kestrel handled, step by step. Ratings decide what becomes training data.</p>
        </div>
        <label className="relative w-full sm:w-72">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-stone-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search requests, answers, ids"
            className="h-9 w-full rounded-lg bg-white pl-9 pr-3 text-sm ring-1 ring-stone-300 focus:outline-none focus:ring-2 focus:ring-accent-500 dark:bg-stone-900 dark:ring-stone-700"
          />
        </label>
      </div>

      <Card className="mt-6 overflow-hidden">
        {error ? (
          <div className="p-6 text-sm text-red-600">{error}</div>
        ) : rows === null ? (
          <div className="flex justify-center p-10"><Spinner className="size-5 text-stone-400" /></div>
        ) : rows.length === 0 ? (
          <Empty icon={<ChartGantt className="size-6" />} title={query ? "No matching traces" : "No traces yet"}>
            {query ? "Try a different search." : "Chat with Kestrel and every request will show up here."}
          </Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-sm">
              <thead>
                <tr className="border-b border-stone-200 text-left text-[11px] font-medium uppercase tracking-wider text-stone-500 dark:border-stone-800">
                  <th className="px-4 py-2.5 font-medium">When</th>
                  <th className="px-4 py-2.5 font-medium">Request</th>
                  <th className="px-4 py-2.5 text-right font-medium">Steps</th>
                  <th className="px-4 py-2.5 text-right font-medium">Tokens</th>
                  <th className="px-4 py-2.5 text-right font-medium">Latency</th>
                  <th className="px-4 py-2.5 font-medium">Provider</th>
                  <th className="px-4 py-2.5 font-medium">Rating</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-stone-100 dark:divide-stone-800/70">
                {rows.map((t) => (
                  <tr key={t.trace_id} onClick={() => open(t.trace_id)} className="cursor-pointer hover:bg-stone-50 dark:hover:bg-stone-800/40">
                    <td className="whitespace-nowrap px-4 py-2.5 tabular-nums text-stone-500">{fmtTime(t.start_time)}</td>
                    <td className="max-w-[28rem] px-4 py-2.5">
                      <div className="flex items-center gap-2">
                        {t.status === "error" && <Badge tone="red">error</Badge>}
                        <span className="truncate">{t.user_message ?? <span className="italic text-stone-400">content not recorded</span>}</span>
                      </div>
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{t.steps ?? "–"}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{fmtInt(t.tokens)}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{fmtMs(t.latency_ms)}</td>
                    <td className="px-4 py-2.5 text-stone-600 dark:text-stone-300">{t.providers.join(", ") || "–"}</td>
                    <td className="px-4 py-2.5">
                      {t.rating === "good" ? <Badge tone="green"><ThumbsUp className="size-3" /> good</Badge> : t.rating === "bad" ? <Badge tone="red"><ThumbsDown className="size-3" /> bad</Badge> : <span className="text-stone-300 dark:text-stone-600">–</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

const SPAN_STYLE: Record<string, { bar: string; dot: string }> = {
  agent_run: { bar: "bg-stone-400/70 dark:bg-stone-500/70", dot: "bg-stone-400" },
  llm_call: { bar: "bg-sky-500", dot: "bg-sky-500" },
  tool_call: { bar: "bg-emerald-500", dot: "bg-emerald-500" },
  approval: { bar: "bg-amber-400", dot: "bg-amber-400" },
};

function spanLabel(s: Span): { title: string; detail: string } {
  const a = s.attributes;
  switch (s.name) {
    case "llm_call":
      return {
        title: `${a["gen_ai.provider.name"] ?? "model"}`,
        detail: `${a["gen_ai.request.model"] ?? ""} · ${fmtInt(a["gen_ai.usage.input_tokens"] as number)} in / ${fmtInt(a["gen_ai.usage.output_tokens"] as number)} out${a["kestrel.usage.estimated"] ? " (est.)" : ""}`,
      };
    case "tool_call":
      return {
        title: String(a["gen_ai.tool.name"] ?? "tool"),
        detail: [a["kestrel.tool.risk"], a["kestrel.tool.server"] && `mcp:${a["kestrel.tool.server"]}`, a["kestrel.tool.ran"] === false && "not run"].filter(Boolean).join(" · "),
      };
    case "approval":
      return { title: `approval: ${a["kestrel.approval.decision"]}`, detail: a["kestrel.approval.reason"] ? `“${a["kestrel.approval.reason"]}”` : "your decision time" };
    default:
      return { title: "agent_run", detail: `${a["kestrel.stop_reason"] ?? ""} · ${a["kestrel.steps"] ?? "?"} steps` };
  }
}

function Waterfall({ spans }: { spans: Span[] }) {
  const [selected, setSelected] = useState<string | null>(null);
  const ordered = useMemo(() => {
    const children = new Map<string | null, Span[]>();
    for (const s of spans) children.set(s.parent_id, [...(children.get(s.parent_id) ?? []), s]);
    const out: { span: Span; depth: number }[] = [];
    const walk = (parent: string | null, depth: number) => {
      for (const s of children.get(parent) ?? []) {
        out.push({ span: s, depth });
        walk(s.span_id, depth + 1);
      }
    };
    walk(null, 0);
    return out;
  }, [spans]);
  const root = ordered[0]?.span;
  if (!root) return null;
  const start = root.start_time;
  const total = Math.max((root.duration_ms ?? 1) / 1000, 0.001);
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  const chosen = spans.find((s) => s.span_id === selected);

  return (
    <Card className="overflow-hidden">
      <div className="overflow-x-auto">
        <div className="min-w-[680px]">
          <div className="grid grid-cols-[17rem_1fr] border-b border-stone-200 text-[11px] text-stone-400 dark:border-stone-800">
            <div className="px-4 py-2 font-medium uppercase tracking-wider">Span</div>
            <div className="relative mr-4 h-8">
              {ticks.map((t) => (
                <span
                  key={t}
                  className={cx("absolute top-2 tabular-nums", t === 0 ? "" : t === 1 ? "-translate-x-full" : "-translate-x-1/2")}
                  style={{ left: `${t * 100}%` }}
                >
                  {fmtMs(t * total * 1000)}
                </span>
              ))}
            </div>
          </div>
          {ordered.map(({ span, depth }) => {
            const left = ((span.start_time - start) / total) * 100;
            const width = Math.max(((span.duration_ms ?? 0) / 1000 / total) * 100, 0.4);
            const { title, detail } = spanLabel(span);
            const style = SPAN_STYLE[span.name] ?? SPAN_STYLE.agent_run;
            return (
              <button
                key={span.span_id}
                onClick={() => setSelected(selected === span.span_id ? null : span.span_id)}
                className={cx(
                  "grid w-full grid-cols-[17rem_1fr] items-center border-b border-stone-100 text-left text-[13px] last:border-b-0 hover:bg-stone-50 dark:border-stone-800/60 dark:hover:bg-stone-800/30",
                  selected === span.span_id && "bg-accent-50/60 dark:bg-accent-950/20",
                )}
              >
                <div className="flex min-w-0 items-center gap-2 py-2 pr-2" style={{ paddingLeft: `${1 + depth * 1.1}rem` }}>
                  <span className={cx("size-2 shrink-0 rounded-full", style.dot)} />
                  <span className="min-w-0">
                    <span className={cx("block truncate font-medium", span.name === "tool_call" && "font-mono text-[12.5px]")}>{title}</span>
                    <span className="block truncate text-[11.5px] text-stone-500">{detail}</span>
                  </span>
                </div>
                <div className="relative mr-4 h-9">
                  {ticks.slice(1, -1).map((t) => (
                    <span key={t} className="absolute inset-y-0 w-px bg-stone-100 dark:bg-stone-800/70" style={{ left: `${t * 100}%` }} />
                  ))}
                  <span
                    className={cx("absolute top-1/2 h-3.5 -translate-y-1/2 rounded", style.bar, span.status === "error" && "bg-red-500 ring-2 ring-red-300 dark:ring-red-800")}
                    style={{ left: `${left}%`, width: `${width}%` }}
                  />
                  <span
                    className="absolute top-1/2 -translate-y-1/2 whitespace-nowrap pl-1.5 text-[11px] tabular-nums text-stone-500"
                    style={left + width > 82 ? { right: `${100 - left}%`, paddingRight: "0.375rem" } : { left: `${left + width}%` }}
                  >
                    {fmtMs(span.duration_ms)}
                  </span>
                </div>
              </button>
            );
          })}
        </div>
      </div>
      {chosen && (
        <div className="border-t border-stone-200 bg-stone-50 p-4 dark:border-stone-800 dark:bg-stone-950/50">
          <div className="mb-2 flex items-center gap-2 text-sm">
            <span className="font-medium">{chosen.name}</span>
            {chosen.status === "error" && <Badge tone="red">error</Badge>}
            <span className="font-mono text-xs text-stone-400">{chosen.span_id}</span>
          </div>
          {chosen.error && <div className="mb-2 rounded-md bg-red-50 p-2 font-mono text-xs text-red-800 dark:bg-red-950/40 dark:text-red-300">{chosen.error}</div>}
          <pre className="max-h-72 overflow-auto rounded-md bg-white p-3 font-mono text-[12px] leading-5 ring-1 ring-stone-200 dark:bg-stone-900 dark:ring-stone-800">
            {JSON.stringify(chosen.attributes, null, 2)}
          </pre>
        </div>
      )}
    </Card>
  );
}

function TraceView({ id, back }: { id: string; back: () => void }) {
  const [trace, setTrace] = useState<TraceDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(
    () =>
      api<TraceDetail>(`/api/traces/${id}`)
        .then(setTrace)
        .catch((e) => setError(String(e.message ?? e))),
    [id],
  );
  useEffect(() => {
    load();
  }, [load]);

  const rate = async (rating: "good" | "bad") => {
    await rateTrace(id, rating, trace?.rating_note ?? "");
    load();
  };

  const tools = trace?.spans.filter((s) => s.name === "tool_call") ?? [];
  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <button onClick={back} className="mb-4 inline-flex items-center gap-1.5 text-sm text-stone-500 hover:text-stone-900 dark:hover:text-stone-100">
        <ArrowLeft className="size-4" /> All traces
      </button>
      {error ? (
        <div className="text-red-600">{error}</div>
      ) : !trace ? (
        <Spinner className="size-5 text-stone-400" />
      ) : (
        <div className="space-y-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="font-mono text-xs text-stone-400">{trace.trace_id}</div>
              <h1 className="mt-1 text-xl font-semibold tracking-tight">{trace.user_message ?? "Content not recorded"}</h1>
              <div className="mt-2 flex flex-wrap gap-1.5">
                <Badge>{new Date(trace.start_time * 1000).toLocaleString()}</Badge>
                <Badge tone={trace.status === "error" ? "red" : "neutral"}>{trace.stop_reason}</Badge>
                {trace.providers.map((p) => <Badge key={p} tone="accent">{p}</Badge>)}
                {tools.map((t) => (
                  <Badge key={t.span_id} tone={riskTone(t.attributes["kestrel.tool.risk"] as string)} className="font-mono">
                    {String(t.attributes["gen_ai.tool.name"])}
                  </Badge>
                ))}
              </div>
            </div>
            <div className="flex items-center gap-1 rounded-lg bg-white p-1 ring-1 ring-stone-200 dark:bg-stone-900 dark:ring-stone-800">
              <button onClick={() => rate("good")} aria-label="Rate good" className={cx("rounded-md p-1.5", trace.rating === "good" ? "bg-emerald-50 text-emerald-600 dark:bg-emerald-950/40" : "text-stone-400 hover:text-stone-700")}>
                <ThumbsUp className="size-4" />
              </button>
              <button onClick={() => rate("bad")} aria-label="Rate bad" className={cx("rounded-md p-1.5", trace.rating === "bad" ? "bg-red-50 text-red-600 dark:bg-red-950/40" : "text-stone-400 hover:text-stone-700")}>
                <ThumbsDown className="size-4" />
              </button>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              ["Latency", fmtMs(trace.latency_ms), trace.wait_ms ? `+ ${fmtMs(trace.wait_ms)} waiting on you` : "no approvals"],
              ["Steps", trace.steps ?? "–", `${tools.length} tool call${tools.length === 1 ? "" : "s"}`],
              ["Tokens", fmtInt(trace.tokens), `${fmtInt(trace.input_tokens)} in / ${fmtInt(trace.output_tokens)} out${trace.tokens_estimated ? " (est.)" : ""}`],
              ["List price", fmtUsd(trace.list_price_usd), `paid ${fmtUsd(trace.cost_usd)}`],
            ].map(([label, value, hint]) => (
              <Card key={String(label)} className="px-4 py-3">
                <div className="text-[11px] font-medium uppercase tracking-wider text-stone-500">{label}</div>
                <div className="mt-1 text-xl font-semibold tabular-nums">{value}</div>
                <div className="text-xs text-stone-500">{hint}</div>
              </Card>
            ))}
          </div>

          <Waterfall spans={trace.spans} />

          {trace.final_answer && (
            <Card className="p-4">
              <div className="mb-1 text-[11px] font-medium uppercase tracking-wider text-stone-500">Answer</div>
              <Markdown text={trace.final_answer} />
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
