// What the nav chip and the HUD say about the wake word's microphone (server status: /api/wake).
import type { WakeStatus } from "./api";

// Shown when the server started without the wake word, instead of nothing: it can't be turned on from here.
export const WAKE_OFF_ON_SERVER =
  "The wake word is off on the server: start Kestrel with KESTREL_WAKE=on to hear \"Kestrel\" (docs/voice-wake.md)";

// "Listening", "Listening (battery 64%)", "Paused: battery low (28%)", "Paused: power saver", ...
export function wakeLabel(status: WakeStatus): string {
  if (status.listening) {
    return status.on_ac === false && status.battery != null ? `Listening (battery ${status.battery}%)` : "Listening";
  }
  if (!status.enabled) return "Wake word off";
  return status.paused ? `Paused: ${status.paused}` : "Starting…";
}
