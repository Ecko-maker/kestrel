// A spoken request's send countdown (server: wake/service.py _hold_request). The server owns the timer:
// it sends `voice_pending`, then `voice_request` when the countdown runs out (the console then sends it
// like typed text), or `voice_cancelled` when Esc, the Cancel button or a spoken "cancel" stopped it.
// Cancelling only ever stops this request; it can't answer an approval card (those need a click).

export interface PendingSend {
  id: string;
  text: string;
  seconds: number;
  receivedAt: number; // this browser's time
}

export type CancelBy = "esc" | "button";

interface VoiceEvent {
  type: string;
  [key: string]: unknown;
}

// The countdown as the console shows it: voice_pending starts it; voice_request (sent) or
// voice_cancelled (stopped) for the same request ends it.
export function pendingAfter(current: PendingSend | null, event: VoiceEvent, now: number): PendingSend | null {
  if (event.type === "voice_pending") {
    return { id: String(event.id), text: String(event.text), seconds: Number(event.seconds) || 2, receivedAt: now };
  }
  if (event.type === "voice_request") return null;
  if (event.type === "voice_cancelled" && (!current || current.id === event.id)) return null;
  return current;
}

export function cancelMessage(pending: PendingSend, by: CancelBy) {
  return { type: "voice_cancel", id: pending.id, by };
}

export function isCancelKey(key: string): boolean {
  return key === "Escape";
}

export function secondsLeft(pending: PendingSend, now: number): number {
  return Math.max(0, pending.seconds - (now - pending.receivedAt) / 1000);
}

// After a cancel, the text goes into the message box, unsent.
export function boxAfterCancel(event: VoiceEvent): { text: string; why: "cancelled" } {
  return { text: String(event.text ?? ""), why: "cancelled" };
}
