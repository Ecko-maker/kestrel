import { useEffect, useRef, useState } from "react";
import { ArrowUp, FlaskConical, RefreshCw, ThumbsDown, ThumbsUp, TriangleAlert } from "lucide-react";
import { rateTrace, type SessionInfo, type Stats } from "../api";
import { AgentTrace } from "../components/AgentTrace";
import { ApprovalCard } from "../components/ApprovalCard";
import { ImageAllowlist, Markdown } from "../components/Markdown";
import { StatsPanel } from "../components/StatsPanel";
import { Button, cx } from "../components/ui";
import type { Turn, useChat } from "../useChat";

type Chat = ReturnType<typeof useChat>;

const SUGGESTIONS = [
  "Create a note called 'Kestrel ideas' with three ideas for features",
  "What files do I have, and summarize notes.txt",
  "Search the web for the latest Python release",
  "What's 17.5% of 2,340?",
];

function Rating({ traceId, onRated }: { traceId: string; onRated: () => void }) {
  const [rating, setRating] = useState<"good" | "bad" | null>(null);
  const [askNote, setAskNote] = useState(false);
  const [note, setNote] = useState("");
  const [saved, setSaved] = useState(false);

  const rate = async (value: "good" | "bad", withNote = "") => {
    setRating(value);
    await rateTrace(traceId, value, withNote);
    setSaved(true);
    onRated();
  };

  return (
    <div className="mt-2 flex flex-wrap items-center gap-1 text-stone-400">
      <button
        aria-label="Good answer"
        onClick={() => { setAskNote(false); rate("good"); }}
        className={cx("rounded-md p-1.5 hover:bg-stone-100 hover:text-stone-700 dark:hover:bg-stone-800 dark:hover:text-stone-200", rating === "good" && "text-emerald-600 dark:text-emerald-400")}
      >
        <ThumbsUp className="size-4" fill={rating === "good" ? "currentColor" : "none"} />
      </button>
      <button
        aria-label="Bad answer"
        onClick={() => { setAskNote(true); rate("bad"); }}
        className={cx("rounded-md p-1.5 hover:bg-stone-100 hover:text-stone-700 dark:hover:bg-stone-800 dark:hover:text-stone-200", rating === "bad" && "text-red-600 dark:text-red-400")}
      >
        <ThumbsDown className="size-4" fill={rating === "bad" ? "currentColor" : "none"} />
      </button>
      {askNote && (
        <form
          className="ml-1 flex items-center gap-1.5"
          onSubmit={(e) => { e.preventDefault(); rate("bad", note); setAskNote(false); }}
        >
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="What went wrong? (optional)"
            className="h-8 w-56 rounded-md bg-white px-2 text-sm text-stone-800 ring-1 ring-stone-300 focus:outline-none focus:ring-2 focus:ring-accent-500 dark:bg-stone-900 dark:text-stone-100 dark:ring-stone-700"
            autoFocus
          />
          <Button size="sm" type="submit">Save</Button>
        </form>
      )}
      {saved && !askNote && <span className="ml-1 text-xs">Saved to the trace{rating === "good" ? " · it can become training data" : ""}</span>}
    </div>
  );
}

function AssistantTurn({ turn, onRespond, onRated }: { turn: Turn; onRespond: Chat["answerApproval"]; onRated: () => void }) {
  const last = turn.steps[turn.steps.length - 1];
  const streamingText = !turn.done && last && last.tools.length === 0 ? last.text : "";
  const text = turn.answer?.text ?? streamingText;
  const approvals = turn.steps.flatMap((s) => s.tools.flatMap((t) => t.approvals));
  const waitingOnYou = approvals.some((a) => a.status === "pending");

  return (
    <div className="flex gap-3">
      <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-accent-600 shadow-sm">
        <img src="/kestrel.svg" alt="" className="size-7 rounded-lg" />
      </div>
      <div className="min-w-0 flex-1">
        {approvals.length > 0 && (
          <div className="mb-3 space-y-2">
            {approvals.map((a) => <ApprovalCard key={a.id} approval={a} onRespond={onRespond} />)}
          </div>
        )}
        {text ? (
          <Markdown text={text} streaming={!turn.done} />
        ) : !turn.done && !waitingOnYou ? (
          <div className="flex h-7 items-center gap-1">
            {[0, 1, 2].map((i) => (
              <span key={i} className="size-1.5 animate-bounce rounded-full bg-stone-400" style={{ animationDelay: `${i * 120}ms` }} />
            ))}
          </div>
        ) : null}
        {turn.error && !turn.answer && (
          <div className="mt-2 flex items-start gap-2 rounded-lg bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950/40 dark:text-red-300">
            <TriangleAlert className="mt-0.5 size-4 shrink-0" /> {turn.error}
          </div>
        )}
        {turn.steps.length > 0 && <AgentTrace turn={turn} />}
        {turn.done && turn.traceId && turn.answer?.stopReason !== "error" && <Rating traceId={turn.traceId} onRated={onRated} />}
      </div>
    </div>
  );
}

function Composer({ chat, disabled }: { chat: Chat; disabled: boolean }) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  const update = (value: string) => {
    setText(value);
    const el = ref.current; // grow with the text, up to 200px
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  };
  const submit = () => {
    const value = text.trim();
    if (!value || disabled) return;
    chat.send(value);
    setText("");
    if (ref.current) ref.current.style.height = "auto";
  };

  return (
    <div className="rounded-2xl bg-white p-2 ring-1 ring-stone-300 shadow-sm focus-within:ring-2 focus-within:ring-accent-500 dark:bg-stone-900 dark:ring-stone-700">
      <div className="flex items-end gap-2">
        <textarea
          ref={ref}
          rows={1}
          value={text}
          onChange={(e) => update(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder={disabled ? "Kestrel is working…" : "Ask Kestrel anything, or give it a task"}
          className="max-h-[200px] min-h-9 flex-1 resize-none bg-transparent px-2 py-1.5 text-[15px] leading-6 placeholder:text-stone-400 focus:outline-none"
          aria-label="Message"
        />
        <Button variant="primary" className="size-9 shrink-0 rounded-xl px-0" onClick={submit} disabled={disabled || !text.trim()} aria-label="Send">
          <ArrowUp className="size-4" />
        </Button>
      </div>
    </div>
  );
}

const NO_HOSTS: readonly string[] = [];

export function ChatPage({ chat, session, stats, onRated }: { chat: Chat; session: SessionInfo | null; stats: Stats | null; onRated: () => void }) {
  const bottom = useRef<HTMLDivElement>(null);
  const last = chat.turns[chat.turns.length - 1];
  const progress = last ? last.steps.reduce((n, s) => n + s.text.length + s.tools.length * 1000 + s.tools.reduce((m, t) => m + t.approvals.length * 100 + (t.result ? 10 : 0), 0), 0) : 0;
  // Changes whenever the conversation grows, so the effect below keeps the newest content in view.
  const scrollKey = `${chat.turns.length}:${progress}:${last?.done}`;
  useEffect(() => {
    // Braces matter: newer browsers return a Promise from scrollIntoView, and an effect
    // must return nothing or a cleanup function.
    if (scrollKey) bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [scrollKey]);

  const unavailable = session && !session.chat_available;
  const suggestions = session?.demo_prompts?.length ? session.demo_prompts : SUGGESTIONS;
  return (
    <ImageAllowlist.Provider value={session?.image_allowlist ?? NO_HOSTS}>
      <div className="flex h-full min-h-0">
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="min-h-0 flex-1 overflow-y-auto">
            <div className="mx-auto max-w-3xl space-y-8 px-4 py-8 sm:px-6">
              {session?.demo && (
                <div className="flex items-start gap-3 rounded-xl bg-violet-50 px-4 py-3 text-sm text-violet-900 ring-1 ring-violet-200 dark:bg-violet-950/40 dark:text-violet-200 dark:ring-violet-900">
                  <FlaskConical className="mt-0.5 size-4 shrink-0" />
                  <div>
                    <span className="font-semibold">Demo mode.</span>{" "}
                    {(session.demo_notice ?? "").replace(/^Demo mode:\s*(.)/, (_, c: string) => c.toUpperCase())} Add a free Gemini or Groq key to <code>.env</code> to use a real model.
                  </div>
                </div>
              )}
              {chat.turns.length === 0 && (
                <div className="pt-[8vh]">
                  <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">What should Kestrel do?</h1>
                  <p className="mt-2 max-w-xl text-stone-500 dark:text-stone-400">
                    It can read your workspace, search and fetch the web, and draft notes and messages. Anything that changes a file or sends something waits for your approval.
                  </p>
                  <div className="mt-6 grid gap-2 sm:grid-cols-2">
                    {suggestions.map((s) => (
                      <button
                        key={s}
                        onClick={() => chat.send(s)}
                        disabled={chat.state !== "open" || !!unavailable}
                        className="rounded-xl bg-white px-4 py-3 text-left text-sm text-stone-700 ring-1 ring-stone-200 transition hover:-translate-y-px hover:ring-accent-300 hover:shadow-sm disabled:opacity-50 dark:bg-stone-900 dark:text-stone-300 dark:ring-stone-800 dark:hover:ring-accent-800"
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {chat.turns.map((turn) => (
                <div key={turn.id} className="space-y-5">
                  <div className="flex justify-end">
                    <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-stone-800 px-4 py-2.5 text-[15px] leading-6 text-stone-50 dark:bg-stone-200 dark:text-stone-900">
                      {turn.user}
                    </div>
                  </div>
                  <AssistantTurn turn={turn} onRespond={chat.answerApproval} onRated={onRated} />
                </div>
              ))}
              <div ref={bottom} />
            </div>
          </div>

          <div className="border-t border-stone-200/70 bg-stone-50/80 px-4 pb-4 pt-3 backdrop-blur dark:border-stone-800/70 dark:bg-stone-950/80 sm:px-6">
            <div className="mx-auto max-w-3xl space-y-2">
              {(chat.notice || unavailable) && (
                <div className="flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900 ring-1 ring-amber-200 dark:bg-amber-950/40 dark:text-amber-200 dark:ring-amber-900">
                  <TriangleAlert className="mt-0.5 size-4 shrink-0" />
                  <span>{chat.notice ?? session?.problem}</span>
                </div>
              )}
              {chat.state === "closed" && (
                <div className="flex items-center justify-between gap-2 rounded-lg bg-stone-100 px-3 py-2 text-sm dark:bg-stone-900">
                  <span>Disconnected. Anything waiting for approval was rejected.</span>
                  <Button size="sm" onClick={chat.reconnect}><RefreshCw className="size-3.5" /> New conversation</Button>
                </div>
              )}
              <Composer chat={chat} disabled={chat.running || chat.state !== "open" || !!unavailable} />
              <p className="text-center text-[11px] text-stone-400">
                Enter to send · Shift+Enter for a new line · runs locally, approvals required for actions
              </p>
            </div>
          </div>
        </div>

        <div className="hidden w-72 shrink-0 overflow-y-auto border-l border-stone-200/70 p-4 dark:border-stone-800/70 xl:block">
          <StatsPanel stats={stats} />
        </div>
      </div>
    </ImageAllowlist.Provider>
  );
}
