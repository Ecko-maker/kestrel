// A 1.2 s start-up sequence when the HUD opens: the rings draw in, the wordmark and three status
// lines fade up (real values), then the overlay fades out. Once per tab, never under reduced motion.
// It can't be focused or clicked, so it never blocks typing.

import { useEffect, useState } from "react";
import type { SessionInfo } from "../api";
import type { ConnectionState } from "../useChat";
import { Reactor } from "./Reactor";
import { prefersReducedMotion, storageGet, storageSet } from "./useLook";

const KEY = "kestrel-hud-booted";
const tab = () => sessionStorage;

export function BootSequence({ session, connection }: { session: SessionInfo | null; connection: ConnectionState }) {
  const [show, setShow] = useState(() => !prefersReducedMotion() && storageGet(KEY, tab) !== "1");
  useEffect(() => {
    if (!show) return;
    storageSet(KEY, "1", tab);
    const id = setTimeout(() => setShow(false), 1250);
    return () => clearTimeout(id);
  }, [show]);
  if (!show) return null;

  const model = session?.models[0];
  const lines = [
    `LINK ${connection === "open" ? "OK" : "…"}`,
    `MODEL ${model ? `${model.provider} / ${model.model}` : "…"}`,
    `TOOLS ${session ? session.tools.length : "…"} · APPROVALS REQUIRED`,
  ];
  return (
    <div className="hud-boot pointer-events-none fixed inset-0 z-50 flex flex-col items-center justify-center bg-stone-950" aria-hidden="true">
      <Reactor phase="idle" segments={[]} activeTool={null} className="w-56" />
      <div className="hud-wordmark boot-line mt-4 pl-[0.32em] text-3xl" style={{ animationDelay: "250ms" }}>
        KESTREL
      </div>
      <div className="mt-4 space-y-1 font-mono text-[12px] text-accent-200">
        {lines.map((l, i) => (
          <div key={l} className="boot-line" style={{ animationDelay: `${450 + i * 120}ms` }}>
            {l}
          </div>
        ))}
      </div>
    </div>
  );
}
