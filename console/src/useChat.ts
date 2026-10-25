// The chat connection: one WebSocket = one conversation with Kestrel.
// Server events (see src/kestrel/agent.py) are folded into a list of turns by `reduce`.

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import type { WakeStatus } from "./api";
import { boxAfterCancel, cancelMessage, isCancelKey, pendingAfter, type CancelBy, type PendingSend } from "./sending.ts"; // .ts: node --test loads useChat.ts directly

export type ApprovalStatus = "pending" | "approved" | "edited" | "rejected";

// Why a card appeared (tiers v2): reasons, the call's arguments decoded, local text found in them,
// and recipients the user never typed. The audit log and traces keep only hashes and lengths.
export interface ApprovalNotice {
  reasons: string[];
  decoded: string[];
  highlights: { before: string; match: string; after: string; source: string; chars: number }[];
  recipient_warnings: string[];
}

export interface Approval {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  preview: string;
  kind: "diff" | "message" | "external" | "text";
  editableField: string | null;
  allowSession: boolean;
  timeoutS: number;
  receivedAt: number;
  status: ApprovalStatus;
  reason?: string;
  notice?: ApprovalNotice | null;
}

export interface ToolRun {
  callId: string;
  name: string;
  arguments: string;
  risk: string | null;
  server: string | null;
  external: boolean;
  startedAt: number;
  result?: { ok: boolean; ran: boolean; decision: string; result: string; durationMs: number | null };
  approvals: Approval[];
}

export interface Step {
  step: number;
  startedAt: number;
  text: string;
  llm?: {
    ok: boolean;
    provider?: string;
    model?: string;
    durationMs: number | null;
    inputTokens?: number;
    outputTokens?: number;
    estimated?: boolean;
    fallback?: boolean;
    retries?: number;
    error?: string;
  };
  tools: ToolRun[];
}

export interface Answer {
  text: string;
  stopReason: string;
  steps: number;
  tokens: number;
  durationMs: number | null;
  providers: string[];
  listPriceUsd: number | null;
}

export interface Turn {
  id: string;
  user: string;
  traceId?: string;
  startedAt: number;
  steps: Step[];
  answer?: Answer;
  error?: string;
  done: boolean;
  voice?: boolean; // a spoken request (shown with a mic icon); the agent gets the same text either way
}

type ServerEvent = { type: string; trace_id?: string; [key: string]: unknown };

type Action =
  | { kind: "send"; text: string; voice?: boolean }
  | { kind: "event"; event: ServerEvent }
  | { kind: "localApproval"; approvalId: string; status: ApprovalStatus };

const now = () => Date.now();

function updateLast(turns: Turn[], fn: (t: Turn) => Turn): Turn[] {
  if (!turns.length) return turns;
  return [...turns.slice(0, -1), fn(turns[turns.length - 1])];
}

function updateStep(turn: Turn, step: number, fn: (s: Step) => Step): Turn {
  return { ...turn, steps: turn.steps.map((s) => (s.step === step ? fn(s) : s)) };
}

function mapApprovals(turn: Turn, fn: (a: Approval) => Approval): Turn {
  return {
    ...turn,
    steps: turn.steps.map((s) => ({ ...s, tools: s.tools.map((t) => ({ ...t, approvals: t.approvals.map(fn) })) })),
  };
}

export function reduce(turns: Turn[], action: Action): Turn[] {
  if (action.kind === "send") {
    const turn: Turn = { id: crypto.randomUUID(), user: action.text, startedAt: now(), steps: [], done: false };
    return [...turns, action.voice ? { ...turn, voice: true } : turn];
  }
  if (action.kind === "localApproval") {
    return updateLast(turns, (t) =>
      mapApprovals(t, (a) => (a.id === action.approvalId ? { ...a, status: action.status } : a)),
    );
  }
  const e = action.event;
  return updateLast(turns, (turn) => {
    turn = e.trace_id && !turn.traceId ? { ...turn, traceId: e.trace_id } : turn;
    switch (e.type) {
      case "step_started":
        return { ...turn, steps: [...turn.steps, { step: e.step as number, startedAt: now(), text: "", tools: [] }] };
      case "text_delta":
        return updateStep(turn, e.step as number, (s) => ({ ...s, text: s.text + (e.text as string) }));
      case "llm_call":
        return updateStep(turn, e.step as number, (s) => ({
          ...s,
          llm: {
            ok: e.ok as boolean,
            provider: e.provider as string | undefined,
            model: e.model as string | undefined,
            durationMs: e.duration_ms as number | null,
            inputTokens: e.input_tokens as number | undefined,
            outputTokens: e.output_tokens as number | undefined,
            estimated: e.estimated as boolean | undefined,
            fallback: e.fallback as boolean | undefined,
            retries: e.retries as number | undefined,
            error: e.error as string | undefined,
          },
        }));
      case "tool_call":
        return updateStep(turn, e.step as number, (s) => ({
          ...s,
          tools: [
            ...s.tools,
            {
              callId: e.call_id as string,
              name: e.name as string,
              arguments: e.arguments as string,
              risk: e.risk as string | null,
              server: e.server as string | null,
              external: Boolean(e.external),
              startedAt: now(),
              approvals: [],
            },
          ],
        }));
      case "tool_result":
        return updateStep(turn, e.step as number, (s) => ({
          ...s,
          tools: s.tools.map((t) =>
            t.callId === e.call_id
              ? {
                  ...t,
                  result: {
                    ok: e.ok as boolean,
                    ran: e.ran as boolean,
                    decision: e.decision as string,
                    result: e.result as string,
                    durationMs: e.duration_ms as number | null,
                  },
                }
              : t,
          ),
        }));
      case "approval_required": {
        const approval: Approval = {
          id: e.approval_id as string,
          tool: e.tool as string,
          args: e.args as Record<string, unknown>,
          preview: e.preview as string,
          kind: e.kind as Approval["kind"],
          editableField: e.editable_field as string | null,
          allowSession: Boolean(e.allow_session),
          timeoutS: (e.timeout_s as number) ?? 300,
          receivedAt: now(),
          status: "pending",
          notice: (e.notice as ApprovalNotice | null) ?? null,
        };
        // Attach it to the newest call of that tool that hasn't finished yet.
        const steps = [...turn.steps];
        for (let i = steps.length - 1; i >= 0; i--) {
          const idx = steps[i].tools.toReversed().findIndex((t) => t.name === approval.tool && !t.result);
          if (idx >= 0) {
            const toolIdx = steps[i].tools.length - 1 - idx;
            const tools = [...steps[i].tools];
            tools[toolIdx] = { ...tools[toolIdx], approvals: [...tools[toolIdx].approvals, approval] };
            steps[i] = { ...steps[i], tools };
            return { ...turn, steps };
          }
        }
        return turn;
      }
      case "approval_resolved":
        return mapApprovals(turn, (a) =>
          a.id === e.approval_id ? { ...a, status: e.decision as ApprovalStatus, reason: e.reason as string } : a,
        );
      case "answer":
        return {
          ...turn,
          answer: {
            text: e.text as string,
            stopReason: e.stop_reason as string,
            steps: e.steps as number,
            tokens: e.tokens as number,
            durationMs: e.duration_ms as number | null,
            providers: (e.providers as string[]) ?? [],
            listPriceUsd: e.list_price_usd as number | null,
          },
        };
      case "error":
        return { ...turn, error: e.message as string };
      case "done":
        return { ...turn, done: true };
      default:
        return turn;
    }
  });
}

export type ConnectionState = "connecting" | "open" | "closed";

// The wake word was heard (server event `wake`). Kept apart from the turns: a wake is never a chat
// message, never starts a model call and never answers an approval.
export interface Wake {
  key: string; // changes once per wake: drives the fly-in and Listening mode (useHud)
  at: number; // server time
  receivedAt: number; // this browser's time, compared with when a message was sent
  greeting: string;
  listenSeconds: number; // the Listening window (KESTREL_WAKE_LISTEN_SECONDS)
}

// The live mic level (0-1) during Listening, from `wake_level` events (about 10 a second). A tiny
// store outside React state, so only the rings that draw it re-render, not the whole chat.
type Listener = () => void;
const levelListeners = new Set<Listener>();
let levelValue = 0;
export const micLevel = {
  get: () => levelValue,
  set(value: number) {
    levelValue = value;
    levelListeners.forEach((listener) => listener());
  },
  subscribe(listener: Listener) {
    levelListeners.add(listener);
    return () => void levelListeners.delete(listener);
  },
};

// "Speak answers" (spoken requests only), remembered per browser; storage may be blocked.
const SPEAK_KEY = "kestrel-speak-answers";
function readSpeak(): boolean {
  try {
    return localStorage.getItem(SPEAK_KEY) !== "off";
  } catch {
    return true;
  }
}

export function useChat(onTurnDone?: () => void) {
  const [turns, dispatch] = useReducer(reduce, []);
  const [state, setState] = useState<ConnectionState>("connecting");
  const [notice, setNotice] = useState<string | null>(null);
  const [wake, setWakeEvent] = useState<Wake | null>(null);
  const [wakeStatus, setWakeStatus] = useState<WakeStatus>({ available: false });
  const [dismissedWake, setDismissedWake] = useState<string | null>(null); // a wake ended by typing
  const [speakAnswers, setSpeak] = useState(readSpeak);
  const [voiceNote, setVoiceNote] = useState<{ key: string; text: string } | null>(null);
  // A spoken request heard with low confidence ("check"), or one whose send countdown was cancelled:
  // shown unsent in the message box.
  const [heardCheck, setHeardCheck] = useState<{ key: string; text: string; why: "check" | "cancelled" } | null>(null);
  // A spoken request in its 2-second "Sending: ..." countdown (sending.ts).
  const [pendingSend, setPendingSend] = useState<PendingSend | null>(null);
  const speakRef = useRef(speakAnswers);
  const wakeKey = useRef<string | null>(null);
  const sendRef = useRef<(text: string, voice?: boolean) => void>(() => undefined);
  const wakes = useRef(0);
  const socket = useRef<WebSocket | null>(null);
  const doneRef = useRef(onTurnDone);
  useEffect(() => {
    doneRef.current = onTurnDone; // refs are updated after render, never during it
  }, [onTurnDone]);

  // Opens a socket; state changes happen in its event handlers, not synchronously here.
  const open = useCallback(() => {
    socket.current?.close();
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${window.location.host}/ws`);
    socket.current = ws;
    ws.addEventListener("open", () => setState("open"));
    ws.addEventListener("close", () => {
      if (socket.current === ws) setState("closed");
    });
    ws.addEventListener("message", (msg) => {
      const event = JSON.parse(msg.data) as ServerEvent;
      if (event.type === "ready") return setNotice(null);
      if (event.type === "wake_status") return setWakeStatus(event as unknown as WakeStatus);
      if (event.type === "wake_level") return micLevel.set(event.level as number);
      // A spoken request, already transcribed by the server: sent exactly like typed text.
      if (event.type === "voice_pending") return setPendingSend((p) => pendingAfter(p, event, Date.now()));
      if (event.type === "voice_cancelled") {
        setPendingSend((p) => pendingAfter(p, event, Date.now()));
        wakes.current += 1;
        return setHeardCheck({ key: `cancel-${wakes.current}`, ...boxAfterCancel(event) });
      }
      if (event.type === "voice_request") {
        setPendingSend(null);
        return sendRef.current(event.text as string, true);
      }
      if (event.type === "wake_end") return setDismissedWake(wakeKey.current); // the request was recorded
      if (event.type === "voice_check") {
        wakes.current += 1;
        return setHeardCheck({ key: `check-${wakes.current}`, text: event.text as string, why: "check" });
      }
      if (event.type === "voice_ignored") {
        wakes.current += 1;
        return setVoiceNote({ key: `note-${wakes.current}`, text: "I didn't catch that." });
      }
      if (event.type === "wake") {
        wakes.current += 1;
        wakeKey.current = `wake-${wakes.current}`;
        micLevel.set(0);
        return setWakeEvent({
          key: `wake-${wakes.current}`,
          at: event.at as number,
          receivedAt: Date.now(),
          greeting: event.greeting as string,
          listenSeconds: (event.listen_seconds as number) ?? 8,
        });
      }
      if (event.type === "error" && !event.trace_id) return setNotice(event.message as string);
      dispatch({ kind: "event", event });
      if (event.type === "done") doneRef.current?.();
    });
  }, []);

  useEffect(() => {
    open();
    return () => socket.current?.close();
  }, [open]);


  const connect = useCallback(() => {
    setState("connecting"); // a user-initiated reconnect: show it right away
    open();
  }, [open]);

  const send = useCallback((text: string, voice = false) => {
    if (socket.current?.readyState !== WebSocket.OPEN) return;
    dispatch({ kind: "send", text, voice });
    // The voice flag and "Speak answers" are for the server only; the agent receives just the text.
    const extra = voice ? { voice: true, speak: speakRef.current } : {};
    socket.current.send(JSON.stringify({ type: "user_message", text, ...extra }));
  }, []);
  useEffect(() => {
    sendRef.current = send;
  }, [send]);

  const setSpeakAnswers = useCallback((on: boolean) => {
    setSpeak(on);
    speakRef.current = on;
    try {
      localStorage.setItem(SPEAK_KEY, on ? "on" : "off");
    } catch {
      // not remembered; the switch still works for this page
    }
  }, []);

  // Typing ends the wake's Listening mode, here and on the server (which stops sending levels).
  const dismissWake = useCallback(() => {
    if (!wake || dismissedWake === wake.key) return;
    setDismissedWake(wake.key);
    if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(JSON.stringify({ type: "wake_dismiss" }));
  }, [wake, dismissedWake]);

  // Esc or the Cancel button: the server stops the countdown and returns the text (voice_cancelled).
  const cancelSend = useCallback(
    (by: CancelBy) => {
      if (pendingSend && socket.current?.readyState === WebSocket.OPEN) {
        socket.current.send(JSON.stringify(cancelMessage(pendingSend, by)));
      }
    },
    [pendingSend],
  );
  useEffect(() => {
    if (!pendingSend) return;
    const onKey = (e: KeyboardEvent) => {
      if (!isCancelKey(e.key)) return;
      e.preventDefault();
      cancelSend("esc");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pendingSend, cancelSend]);

  const answerApproval = useCallback(
    (approvalId: string, response: { decision: "approve" | "edit" | "reject"; args?: unknown; reason?: string; for_session?: boolean }) => {
      socket.current?.send(JSON.stringify({ type: "approval_response", approval_id: approvalId, ...response }));
      if (response.decision === "edit") dispatch({ kind: "localApproval", approvalId, status: "edited" });
    },
    [],
  );

  const running = turns.length > 0 && !turns[turns.length - 1].done;
  return {
    turns,
    state,
    notice,
    running,
    send,
    answerApproval,
    reconnect: connect,
    wake,
    wakeStatus,
    setWakeStatus,
    wakeDismissed: wake !== null && dismissedWake === wake.key,
    dismissWake,
    speakAnswers,
    setSpeakAnswers,
    voiceNote,
    heardCheck,
    clearHeardCheck: useCallback(() => setHeardCheck(null), []),
    pendingSend,
    cancelSend,
  };
}
