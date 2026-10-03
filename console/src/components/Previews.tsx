// How an approval's preview is shown: a real diff for file changes, the full message for
// emails, and plain text for everything else.

import { cx } from "./ui";

export function DiffView({ preview }: { preview: string }) {
  const lines = preview.split("\n");
  const isNew = lines[0]?.startsWith("New file:");
  const title = isNew
    ? lines[0].replace("New file:", "").trim()
    : (lines.find((l) => l.startsWith("+++ ")) ?? "").replace("+++ ", "").replace(" (after)", "");
  const body = isNew ? lines.slice(1) : lines.filter((l) => !l.startsWith("--- ") && !l.startsWith("+++ "));
  let added = 0;
  let removed = 0;
  for (const l of body) {
    if (l.startsWith("+")) added++;
    else if (l.startsWith("-")) removed++;
  }

  return (
    <div className="overflow-hidden rounded-lg ring-1 ring-stone-200 dark:ring-stone-800">
      <div className="flex items-center justify-between gap-2 border-b border-stone-200 bg-stone-50 px-3 py-1.5 text-xs dark:border-stone-800 dark:bg-stone-900">
        <span className="truncate font-mono text-stone-700 dark:text-stone-300">{title}</span>
        <span className="shrink-0 font-mono">
          {isNew && <span className="mr-2 text-stone-500">new file</span>}
          <span className="text-emerald-600 dark:text-emerald-400">+{added}</span>{" "}
          <span className="text-red-600 dark:text-red-400">−{removed}</span>
        </span>
      </div>
      <div className="max-h-80 overflow-auto bg-white font-mono text-[12.5px] leading-5 dark:bg-stone-950">
        {body.map((line, i) => {
          const kind = line.startsWith("@@") ? "hunk" : line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : "ctx";
          return (
            <div
              key={i}
              className={cx(
                "flex whitespace-pre-wrap break-all px-3",
                kind === "add" && "bg-emerald-50 text-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200",
                kind === "del" && "bg-red-50 text-red-900 dark:bg-red-950/40 dark:text-red-200",
                kind === "hunk" && "bg-sky-50 text-sky-700 dark:bg-sky-950/30 dark:text-sky-300",
                kind === "ctx" && "text-stone-600 dark:text-stone-400",
              )}
            >
              <span className="mr-3 w-3 shrink-0 select-none text-stone-400">
                {kind === "add" ? "+" : kind === "del" ? "−" : ""}
              </span>
              <span>{kind === "add" || kind === "del" ? line.slice(1) || " " : line || " "}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function MessagePreview({ args }: { args: Record<string, unknown> }) {
  return (
    <div className="overflow-hidden rounded-lg ring-1 ring-stone-200 dark:ring-stone-800">
      <dl className="grid grid-cols-[4.5rem_1fr] gap-y-1 border-b border-stone-200 bg-stone-50 px-3 py-2 text-sm dark:border-stone-800 dark:bg-stone-900">
        <dt className="text-stone-500">To</dt>
        <dd className="font-medium">{String(args.to ?? "")}</dd>
        <dt className="text-stone-500">Subject</dt>
        <dd className="font-medium">{String(args.subject ?? "")}</dd>
      </dl>
      <div className="max-h-80 overflow-auto whitespace-pre-wrap bg-white px-3 py-3 text-sm leading-6 dark:bg-stone-950">
        {String(args.body ?? "")}
      </div>
      <div className="border-t border-stone-200 bg-stone-50 px-3 py-1.5 text-xs text-stone-500 dark:border-stone-800 dark:bg-stone-900">
        Simulated for now: on approval it's saved to workspace/outbox/, not actually sent.
      </div>
    </div>
  );
}

export function TextPreview({ preview }: { preview: string }) {
  return (
    <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-lg bg-stone-50 p-3 font-mono text-[12.5px] leading-5 text-stone-700 ring-1 ring-stone-200 dark:bg-stone-950 dark:text-stone-300 dark:ring-stone-800">
      {preview}
    </pre>
  );
}
