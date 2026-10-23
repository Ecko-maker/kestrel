// The wake word's switch and privacy indicator, in the nav in both looks. Whenever the microphone
// is open it says so in red ("Listening"); the server sends that status before the stream opens and
// again after it closes. Nothing here when KESTREL_WAKE is off on the server.

import { useEffect, useState } from "react";
import { Mic, MicOff } from "lucide-react";
import type { WakeStatus } from "../api";
import type { Wake } from "../useChat";
import { cx } from "./ui";

export function wakeLabel(status: WakeStatus): string {
  if (status.listening) return "Listening";
  if (!status.enabled) return "Wake word off";
  return status.paused ? `Paused: ${status.paused}` : "Starting…";
}

export function WakeChip({ status, onToggle }: { status: WakeStatus; onToggle: (enabled: boolean) => void }) {
  if (!status.available) return null;
  const label = wakeLabel(status);
  return (
    <div className="flex flex-col gap-0.5 text-[11px]">
      <button
        role="switch"
        aria-checked={!!status.enabled}
        aria-label={`Wake word: ${label}. ${status.enabled ? "Turn off" : "Turn on"}`}
        title={status.keeping_awake ? `${label} · keeping PC awake` : label}
        onClick={() => onToggle(!status.enabled)}
        className={cx(
          "inline-flex items-center gap-1.5 rounded-md px-2 py-1 font-semibold ring-1 ring-inset",
          status.listening
            ? "bg-red-50 text-red-800 ring-red-200 dark:bg-red-950/50 dark:text-red-200 dark:ring-red-900"
            : "bg-stone-100 text-stone-600 ring-stone-200 dark:bg-stone-800/80 dark:text-stone-300 dark:ring-stone-700",
        )}
      >
        {status.listening ? <Mic className="size-3.5" /> : <MicOff className="size-3.5" />}
        <span className="hidden sm:inline" aria-live="polite">
          {label}
        </span>
        {status.listening && <span className="size-1.5 rounded-full bg-red-500 motion-safe:animate-pulse" aria-hidden="true" />}
      </button>
      {status.keeping_awake && <span className="hidden text-stone-500 md:block dark:text-stone-400">Keeping PC awake</span>}
    </div>
  );
}

// Classic look: the greeting as a short caption (the HUD shows it under the rings instead).
export function WakeCaption({ wake }: { wake: Wake | null }) {
  const [hidden, setHidden] = useState<string | null>(null); // the wake whose caption has timed out
  useEffect(() => {
    if (!wake) return;
    const timer = setTimeout(() => setHidden(wake.key), 5000);
    return () => clearTimeout(timer);
  }, [wake]);
  if (!wake || hidden === wake.key) return null;
  return (
    <div role="status" className="fixed right-4 top-4 z-40 rounded-lg bg-white px-4 py-2 text-sm shadow-lg ring-1 ring-stone-200 dark:bg-stone-900 dark:ring-stone-700">
      {wake.greeting}
    </div>
  );
}
