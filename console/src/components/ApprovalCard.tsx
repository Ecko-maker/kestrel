import { useEffect, useState } from "react";
import { Ban, Check, CircleCheck, CircleX, Pencil, ShieldAlert, Timer, TriangleAlert } from "lucide-react";
import type { Approval, ApprovalNotice } from "../useChat";
import { DiffView, MessagePreview, TextPreview } from "./Previews";
import { Badge, Button, cx } from "./ui";

type Respond = (
  approvalId: string,
  response: { decision: "approve" | "edit" | "reject"; args?: unknown; reason?: string; for_session?: boolean },
) => void;

const TITLES: Record<string, string> = {
  write_file: "Write a file",
  append_to_file: "Add to a file",
  create_note: "Create a note",
  send_message: "Send a message",
};

function useCountdown(approval: Approval): number {
  const deadline = approval.receivedAt + approval.timeoutS * 1000;
  const [left, setLeft] = useState(() => deadline - Date.now());
  useEffect(() => {
    if (approval.status !== "pending") return;
    const id = setInterval(() => setLeft(deadline - Date.now()), 1000);
    return () => clearInterval(id);
  }, [deadline, approval.status]);
  return Math.max(0, Math.round(left / 1000));
}

// Tiers v2: why this card appeared, with the evidence. Text only: nothing here loads anything.
function NoticePanel({ notice }: { notice: ApprovalNotice }) {
  return (
    <div className="space-y-2 rounded-lg bg-amber-50/70 p-3 text-[13px] text-amber-950 ring-1 ring-amber-200 dark:bg-amber-950/20 dark:text-amber-100 dark:ring-amber-900/60">
      {notice.reasons.map((r) => (
        <p key={r} className="flex gap-2">
          <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-amber-600" />
          <span>{r}</span>
        </p>
      ))}
      {notice.highlights.map((h) => (
        <div key={`${h.source}:${h.match}`}>
          <div className="text-xs font-medium text-amber-800 dark:text-amber-300">
            {h.chars} characters from {h.source}, in what this call sends:
          </div>
          <div className="mt-0.5 break-all font-mono text-xs">
            {h.before}
            <mark className="rounded bg-red-200 px-0.5 text-red-950 dark:bg-red-900 dark:text-red-50">{h.match}</mark>
            {h.after}
          </div>
        </div>
      ))}
      {notice.decoded.length > 0 && (
        <div>
          <div className="text-xs font-medium text-amber-800 dark:text-amber-300">Decoded, as the receiving server would read it:</div>
          {notice.decoded.map((d) => (
            <div key={d} className="break-all font-mono text-xs">
              {d}
            </div>
          ))}
        </div>
      )}
      {notice.recipient_warnings.map((w) => (
        <p key={w} className="font-semibold text-red-700 dark:text-red-300">
          {w}
        </p>
      ))}
    </div>
  );
}

export function ApprovalCard({ approval, onRespond }: { approval: Approval; onRespond: Respond }) {
  const [mode, setMode] = useState<"view" | "edit" | "reject">("view");
  const field = approval.editableField;
  const [draft, setDraft] = useState(field ? String(approval.args[field] ?? "") : "");
  const [reason, setReason] = useState("");
  const secondsLeft = useCountdown(approval);
  const pending = approval.status === "pending";

  if (!pending) {
    const labels: Record<Approval["status"], string> = {
      pending: "Waiting",
      approved: "Approved",
      edited: "Edited, showing the new version",
      rejected: "Rejected",
    };
    const label = labels[approval.status];
    return (
      <div className="flex items-center gap-2 rounded-lg bg-stone-100/70 px-3 py-2 text-[13px] text-stone-600 dark:bg-stone-900 dark:text-stone-400">
        {approval.status === "rejected" ? (
          <CircleX className="size-4 text-red-500" />
        ) : (
          <CircleCheck className="size-4 text-emerald-500" />
        )}
        <span>
          {label}: <span className="font-mono">{approval.tool}</span>
          {approval.reason && <span className="text-stone-500"> · “{approval.reason}”</span>}
        </span>
      </div>
    );
  }

  return (
    <div data-approval={approval.tool} className="overflow-hidden rounded-xl bg-white ring-2 ring-amber-300/80 shadow-lg shadow-amber-900/5 dark:bg-stone-900 dark:ring-amber-700/60">
      <div className="flex flex-wrap items-center gap-2 border-b border-amber-200/70 bg-amber-50 px-4 py-2.5 dark:border-amber-900/50 dark:bg-amber-950/30">
        <ShieldAlert className="size-4 text-amber-600 dark:text-amber-400" />
        <span className="font-medium">{TITLES[approval.tool] ?? "Approve this action"}</span>
        <Badge tone="amber" className="font-mono">
          {approval.tool}
        </Badge>
        <span className="ml-auto flex items-center gap-1 text-xs tabular-nums text-amber-700 dark:text-amber-400">
          <Timer className="size-3.5" />
          {Math.floor(secondsLeft / 60)}:{String(secondsLeft % 60).padStart(2, "0")} until auto-reject
        </span>
      </div>

      <div className="space-y-3 p-4">
        {approval.notice && <NoticePanel notice={approval.notice} />}
        {mode === "edit" && field ? (
          <div>
            <label className="mb-1.5 block text-xs font-medium text-stone-500">
              Edit <span className="font-mono">{field}</span>; you'll see the new preview before anything runs
            </label>
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              rows={Math.min(14, Math.max(4, draft.split("\n").length + 1))}
              className="w-full resize-y rounded-lg bg-stone-50 p-3 font-mono text-[13px] leading-5 ring-1 ring-stone-300 focus:outline-none focus:ring-2 focus:ring-accent-500 dark:bg-stone-950 dark:ring-stone-700"
              autoFocus
            />
          </div>
        ) : approval.kind === "diff" ? (
          <DiffView preview={approval.preview} />
        ) : approval.kind === "message" ? (
          <MessagePreview args={approval.args} />
        ) : (
          <TextPreview preview={approval.preview} />
        )}

        {mode === "reject" && (
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && onRespond(approval.id, { decision: "reject", reason })}
            placeholder="Why not? (optional; Kestrel will read this and adapt)"
            className="h-9 w-full rounded-lg bg-white px-3 text-sm ring-1 ring-stone-300 focus:outline-none focus:ring-2 focus:ring-accent-500 dark:bg-stone-950 dark:ring-stone-700"
            autoFocus
          />
        )}

        <div className={cx("flex flex-wrap items-center gap-2", mode !== "view" && "justify-end")}>
          {mode === "view" && (
            <>
              <Button variant="primary" onClick={() => onRespond(approval.id, { decision: "approve" })}>
                <Check className="size-4" /> Approve
              </Button>
              {approval.allowSession && (
                <Button onClick={() => onRespond(approval.id, { decision: "approve", for_session: true })}>
                  Approve for this session
                </Button>
              )}
              {field && (
                <Button onClick={() => setMode("edit")}>
                  <Pencil className="size-3.5" /> Edit
                </Button>
              )}
              <Button variant="danger" className="sm:ml-auto" onClick={() => setMode("reject")}>
                <Ban className="size-3.5" /> Reject
              </Button>
            </>
          )}
          {mode === "edit" && field && (
            <>
              <Button variant="ghost" onClick={() => setMode("view")}>
                Cancel
              </Button>
              <Button
                variant="primary"
                onClick={() => onRespond(approval.id, { decision: "edit", args: { ...approval.args, [field]: draft } })}
              >
                Preview changes
              </Button>
            </>
          )}
          {mode === "reject" && (
            <>
              <Button variant="ghost" onClick={() => setMode("view")}>
                Cancel
              </Button>
              <Button variant="danger" onClick={() => onRespond(approval.id, { decision: "reject", reason })}>
                <Ban className="size-3.5" /> Reject
              </Button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
