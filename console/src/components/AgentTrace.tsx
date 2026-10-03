// The live "agent trace" under each answer: every model call and tool call as it happens.

import { useState } from "react";
import { Ban, ChevronDown, ChevronRight, CircleCheck, CircleX, Cpu, Plug, Wrench } from "lucide-react";
import { fmtMs, fmtUsd, prettyArgs } from "../format";
import type { Turn } from "../useChat";
import { Badge, Spinner, cx, riskTone } from "./ui";

export function summarize(turn: Turn): string {
  const tools = turn.steps.reduce((n, s) => n + s.tools.length, 0);
  const providers = turn.answer?.providers.length
    ? turn.answer.providers
    : [...new Set(turn.steps.map((s) => s.llm?.provider).filter(Boolean) as string[])];
  const parts = [`${tools} tool${tools === 1 ? "" : "s"}`];
  if (providers.length) parts.push(providers.join(" → "));
  if (turn.answer?.durationMs != null) parts.push(fmtMs(turn.answer.durationMs));
  if (turn.answer) parts.push(turn.answer.listPriceUsd == null ? "no list price" : `${fmtUsd(turn.answer.listPriceUsd)} list price`);
  return parts.join(" · ");
}

export function AgentTrace({ turn }: { turn: Turn }) {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const showing = open || !turn.done;

  return (
    <div className="mt-3 text-[13px]">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 rounded-md py-0.5 pr-1.5 text-stone-500 hover:text-stone-800 dark:text-stone-400 dark:hover:text-stone-200"
      >
        {showing ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        <span className="font-medium">Agent trace</span>
        <span className="text-stone-400 dark:text-stone-500">· {summarize(turn)}</span>
        {!turn.done && <Spinner className="ml-1 text-accent-500" />}
      </button>

      {showing && (
        <ol className="mt-2 space-y-1 border-l border-stone-200 pl-4 dark:border-stone-800">
          {turn.steps.map((step) => (
            <li key={step.step} className="space-y-1">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-stone-600 dark:text-stone-400">
                <Cpu className="size-3.5 text-sky-500" />
                <span>
                  {step.llm?.provider ? (
                    <>
                      <span className="font-medium text-stone-800 dark:text-stone-200">{step.llm.provider}</span>
                      <span className="text-stone-400"> / {step.llm.model}</span>
                    </>
                  ) : (
                    "Thinking"
                  )}
                </span>
                {step.llm ? (
                  <span className="tabular-nums text-stone-400">
                    {fmtMs(step.llm.durationMs)}
                    {step.llm.inputTokens != null &&
                      ` · ${step.llm.inputTokens.toLocaleString()} in / ${step.llm.outputTokens?.toLocaleString()} out${step.llm.estimated ? " (est.)" : ""}`}
                  </span>
                ) : (
                  <Spinner className="text-sky-500" />
                )}
                {step.llm?.fallback && <Badge tone="amber">fallback</Badge>}
                {!!step.llm?.retries && <Badge tone="amber">{step.llm.retries} retries</Badge>}
                {step.llm && !step.llm.ok && <Badge tone="red">failed</Badge>}
              </div>
              {step.tools.length > 0 && step.text.trim() && (
                <div className="pl-5 italic text-stone-500 dark:text-stone-500">“{step.text.trim()}”</div>
              )}
              {step.tools.map((tool) => {
                const r = tool.result;
                const isOpen = expanded === tool.callId;
                return (
                  <div key={tool.callId} className="pl-5">
                    <button
                      onClick={() => setExpanded(isOpen ? null : tool.callId)}
                      className="flex w-full min-w-0 items-center gap-2 rounded px-1 py-0.5 text-left hover:bg-stone-100 dark:hover:bg-stone-900"
                    >
                      {tool.external ? <Plug className="size-3.5 shrink-0 text-violet-500" /> : <Wrench className="size-3.5 shrink-0 text-emerald-600" />}
                      <span className="shrink-0 font-mono font-medium text-stone-800 dark:text-stone-200">{tool.name}</span>
                      <span className="min-w-0 truncate font-mono text-stone-400">({prettyArgs(tool.arguments)})</span>
                      <span className="ml-auto flex shrink-0 items-center gap-1.5">
                        {tool.risk && tool.risk !== "safe" && <Badge tone={riskTone(tool.risk)}>{tool.risk}</Badge>}
                        {tool.server && <Badge tone="violet">mcp:{tool.server}</Badge>}
                        {!r ? (
                          <Spinner className="text-emerald-600" />
                        ) : !r.ran ? (
                          <Ban className={cx("size-3.5", r.decision === "rejected" ? "text-red-500" : "text-stone-400")} />
                        ) : r.ok ? (
                          <CircleCheck className="size-3.5 text-emerald-500" />
                        ) : (
                          <CircleX className="size-3.5 text-red-500" />
                        )}
                        {r && <span className="w-12 text-right tabular-nums text-stone-400">{r.ran ? fmtMs(r.durationMs) : r.decision || "not run"}</span>}
                      </span>
                    </button>
                    {isOpen && r && (
                      <pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap rounded-md bg-stone-100 p-2 font-mono text-[12px] leading-5 text-stone-700 dark:bg-stone-900 dark:text-stone-300">
                        {r.result}
                      </pre>
                    )}
                  </div>
                );
              })}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
