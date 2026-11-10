// What the sidebar's always-visible controls show: "New conversation" (under Chat) and the Speaker switch
// (next to Wake word). Both are always rendered, in both looks; these say when they are disabled, and why.
import type { WakeStatus } from "./api";

export interface ControlState {
  disabled: boolean;
  title: string; // the tooltip: what it does, or why it can't be used now
}

// "New conversation" clears the chat (traces are kept). On an empty chat there's nothing to clear.
export function newConversationState(turns: number, running: boolean, connected: boolean): ControlState {
  if (!connected) return { disabled: true, title: "Not connected to Kestrel" };
  if (turns === 0) return { disabled: true, title: "Nothing to clear yet" };
  if (running) return { disabled: true, title: "Wait for Kestrel to finish, or answer the approval first" };
  return { disabled: false, title: "Start over: Kestrel forgets this chat (its traces are kept)" };
}

// Speech output comes from the wake service on the server (docs/voice-wake.md): without it, or without a
// voice on the server (`speech: false`), the switch stays visible but disabled, with the reason.
export function speakerState(status: WakeStatus, demo: boolean): ControlState {
  if (!status.available) {
    return {
      disabled: true,
      title: demo ? "No speech output in demo mode" : "No speech output: start Kestrel with KESTREL_WAKE=on",
    };
  }
  if (status.speech === false) return { disabled: true, title: "No voice on this server (needs Windows' speech engine)" };
  return { disabled: false, title: "Read answers to spoken requests aloud (off: Kestrel says nothing)" };
}
