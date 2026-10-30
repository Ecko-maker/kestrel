// The wake word's switch and privacy indicator, in the nav in both looks. Whenever the microphone
// is open it says so in red ("Listening"); the server sends that status before the stream opens and
// again after it closes. Nothing here when KESTREL_WAKE is off on the server.

import { useEffect, useState } from "react";
import { Mic, MicOff, Volume2, VolumeX } from "lucide-react";
import type { WakeStatus } from "../api";
import type { Wake } from "../useChat";
import { wakeLabel } from "../wakeLabel";
import { cx } from "./ui";

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

// "Speak answers": answers to spoken requests are read out (first sentences only). Typed questions
// are never spoken, and an approval card is only ever announced, never read.
export function SpeakSwitch({ on, onChange }: { on: boolean; onChange: (on: boolean) => void }) {
  return (
    <button
      role="switch"
      aria-checked={on}
      aria-label={`Speak answers: ${on ? "on" : "off"}`}
      onClick={() => onChange(!on)}
      className="inline-flex items-center gap-1.5 rounded-md bg-stone-100 px-2 py-1 text-[11px] font-semibold text-stone-600 ring-1 ring-inset ring-stone-200 dark:bg-stone-800/80 dark:text-stone-300 dark:ring-stone-700"
    >
      {on ? <Volume2 className="size-3.5" /> : <VolumeX className="size-3.5" />}
      <span className="hidden sm:inline">Speak answers {on ? "on" : "off"}</span>
    </button>
  );
}

// A short caption: the greeting (classic look; the HUD shows it under the rings) or "I didn't catch that".
export function Caption({ item }: { item: { key: string; text: string } | null }) {
  const [hidden, setHidden] = useState<string | null>(null); // the caption that has timed out
  useEffect(() => {
    if (!item) return;
    const timer = setTimeout(() => setHidden(item.key), 5000);
    return () => clearTimeout(timer);
  }, [item]);
  if (!item || hidden === item.key) return null;
  return (
    <div role="status" className="fixed right-4 top-4 z-40 rounded-lg bg-white px-4 py-2 text-sm text-stone-900 shadow-lg ring-1 ring-stone-200 dark:bg-stone-900 dark:text-stone-100 dark:ring-stone-700">
      {item.text}
    </div>
  );
}

export const greetingCaption = (wake: Wake | null) => (wake ? { key: wake.key, text: wake.greeting } : null);
